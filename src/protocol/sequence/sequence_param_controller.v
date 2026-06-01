//=============================================================================
// 序列发生器参数控制器 V4.0（32位DDS）
// 功能：
//   1. 解析CDC命令并配置序列发生器参数
//   2. 支持并行模式（8通道组成字节序列）- 使用旧协议0x30-0x34
//   3. 支持串行模式（每通道独立比特序列+独立频率）- 使用新协议0x40-0x43
//   4. 频率控制（32位DDS频率字，高精度）
// 旧协议（并行模式）：
//   0x30: 并行模式配置 - payload: [序列长度(1B)][序列数据...N字节]
//   0x31: 串行模式配置 - payload: [通道ID(1B)][序列长度(1B)][比特序列...]
//   0x32: 频率控制     - payload: [频率字(4B小端序)]
//   0x33: 启动输出     - payload: 无
//   0x34: 停止输出     - payload: 无
// 新协议（串行模式独立频率 - 32位DDS）：
//   0x40: 配置通道参数 - payload: [通道ID][频率字31:24][23:16][15:8][7:0][长度]
//   0x41: 写入序列数据 - payload: [通道ID][地址][数据]
//   0x42: 使能控制     - payload: [使能掩码]
//   0x43: 全局复位     - payload: 无
//=============================================================================

module sequence_param_controller(
        input clk,
        input rst_n,

        // CDC命令接口
        input [7:0] cmd,
        input [7:0] payload_data,
        input payload_valid,
        input cmd_done,

        // 序列输出
        output [7:0] seq_output,
        output reg seq_enable,

        // 状态输出
        output [7:0] status
    );


    //=========================================================================
    // 命令码定义
    //=========================================================================
    // 旧协议（并行模式 + 串行模式共享频率）
    localparam CMD_SEQ_PARALLEL_MODE  = 8'h30;
    localparam CMD_SEQ_SERIAL_MODE    = 8'h31;
    localparam CMD_SEQ_FREQ_CONTROL   = 8'h32;
    localparam CMD_SEQ_START          = 8'h33;
    localparam CMD_SEQ_STOP           = 8'h34;

    // 新协议（串行模式独立频率）
    localparam CMD_SEQ_CONFIG_CHANNEL = 8'h40;  // 配置通道参数
    localparam CMD_SEQ_WRITE_DATA     = 8'h41;  // 写入序列数据
    localparam CMD_SEQ_ENABLE_CONTROL = 8'h42;  // 使能控制
    localparam CMD_SEQ_RESET_ALL      = 8'h43;  // 全局复位

    //=========================================================================
    // 配置寄存器
    //=========================================================================
    reg mode_parallel;              // 1=并行模式, 0=串行模式
    reg [7:0] seq_length;           // 序列长度（1-256）
    reg [31:0] freq_word;           // 全局频率控制字（旧协议）
    reg [7:0] channel_mask;         // 通道掩码（串行模式）

    // 新协议：每通道独立频率（32位DDS频率字，高精度）
    reg [31:0] freq_word_array [0:7];  // 每通道32位DDS频率字
    integer k;
    initial begin
        for (k = 0; k < 8; k = k + 1) begin
            // 默认1kHz: freq_word = (1000 * 2^32) / 50MHz = 85899
            freq_word_array[k] = 32'd85899;
        end
    end

    //=========================================================================
    // 并行序列RAM（存储字节序列）
    // 深度：256字节
    // 改进：分离写控制逻辑，参考arb_wave_ram_simple.v的成功模式
    //=========================================================================
    reg [7:0] parallel_ram [0:255];
    reg [7:0] parallel_wr_addr;
    reg parallel_wr_en;
    reg [7:0] parallel_wr_data;  // 新增：写数据寄存器

    // RAM初始化（便于调试）
    integer i;
    initial begin
        for (i = 0; i < 256; i = i + 1) begin
            parallel_ram[i] = 8'h00;
        end
        // 测试数据已清除，使用上位机动态写入
    end

    // ⚠️ 关键改进：独立的RAM写入逻辑（参考arb_wave_ram_simple.v）
    // 这样可以避免状态机和RAM写入的时序冲突
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            // 复位时不做任何操作
        end
        else if (parallel_wr_en) begin
            // 只有写使能有效时才写入
            parallel_ram[parallel_wr_addr] <= parallel_wr_data;
        end
    end

    //=========================================================================
    // 串行序列RAM（每通道独立比特序列）
    // 优化：使用8位宽RAM代替256位宽寄存器，减少综合时间
    // 深度：8通道 × 32字节（256位）
    //=========================================================================
    reg [7:0] serial_ram [0:7][0:31];   // 8通道 × 32字节
    reg [7:0] serial_len_array [0:7];    // 每个通道的序列长度
    reg [2:0] serial_channel_id;         // 当前写入的通道
    reg [7:0] serial_wr_addr;            // 字节地址（0-31）
    reg [7:0] serial_byte_count;         // 接收的字节计数
    reg serial_wr_en;

    // 串行RAM初始化
    integer j;
    initial begin
        for (i = 0; i < 8; i = i + 1) begin
            for (j = 0; j < 32; j = j + 1) begin
                serial_ram[i][j] = 8'h0;
            end
            serial_len_array[i] = 8'd8;  // 默认长度8
        end
    end

    //=========================================================================
    // Payload接收状态机
    //=========================================================================
    localparam S_IDLE              = 4'd0;
    localparam S_RECV_SER_LEN      = 4'd1;  // 串行模式：接收序列长度
    localparam S_RECV_SER_DATA     = 4'd2;  // 串行模式：接收比特序列
    localparam S_RECV_FREQ         = 4'd3;  // 频率控制：接收频率字
    localparam S_RECV_NEW_CONFIG   = 4'd4;  // 新协议：接收通道配置
    localparam S_RECV_NEW_DATA     = 4'd5;  // 新协议：接收序列数据

    reg [3:0] state;
    reg [7:0] cmd_reg;              // 保存当前命令
    reg cmd_latched;                // 命令已锁存标志
    reg [15:0] payload_counter;     // Payload计数器
    reg [7:0] serial_seq_len;       // 串行序列长度
    reg [7:0] parallel_write_count; // 并行模式写入计数器（独立）

    // 新协议临时缓冲
    reg [2:0] new_channel_id;       // 新协议通道ID
    reg [31:0] new_freq_word;       // 新协议32位DDS频率字
    reg [7:0] new_length;           // 新协议序列长度
    reg [7:0] new_wr_addr;          // 新协议写地址
    reg [2:0] new_freq_byte_cnt;    // 频率字字节计数（0-3）

    // 频率字接收缓冲（4字节,小端序）
    reg [31:0] freq_buffer;

    //=========================================================================
    // 命令解析与参数接收
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= S_IDLE;
            cmd_reg <= 8'h0;
            cmd_latched <= 1'b0;
            payload_counter <= 16'h0;
            parallel_wr_addr <= 8'h0;
            parallel_wr_en <= 1'b0;
            parallel_wr_data <= 8'h0;
            parallel_write_count <= 8'h0;  // 初始化写入计数器
            serial_wr_addr <= 8'h0;
            serial_byte_count <= 8'h0;
            serial_wr_en <= 1'b0;
            seq_length <= 8'h0;
            freq_word <= 32'h0;
            freq_buffer <= 32'h0;
            mode_parallel <= 1'b1;
            channel_mask <= 8'h0;
            serial_channel_id <= 3'h0;
            serial_seq_len <= 8'h0;
        end
        else begin
            // 默认清除写使能
            parallel_wr_en <= 1'b0;
            serial_wr_en <= 1'b0;

            // 🔧 关键修复：实时RAM写入（完全参考DDS_Param_Controller）
            // 使用独立的write_count，避免地址递增时序问题
            if (payload_valid && cmd == CMD_SEQ_PARALLEL_MODE) begin
                // 第一个字节是序列长度，不写入RAM
                if (!cmd_latched) begin
                    seq_length <= payload_data;
                    parallel_write_count <= 8'h0;  // 重置写入计数器
                    mode_parallel <= 1'b1;
                    cmd_latched <= 1'b1;
                end
                // 后续字节写入RAM
                else begin
                    // 使用write_count作为地址（当前值）
                    parallel_wr_addr <= parallel_write_count;
                    parallel_wr_data <= payload_data;
                    parallel_wr_en <= 1'b1;

                    // 计数器递增（下一个payload使用新值）
                    parallel_write_count <= parallel_write_count + 1;
                end
            end

            // cmd_done信号处理
            if (cmd_done) begin
                cmd_latched <= 1'b0;  // 清除锁存标志
                payload_counter <= 16'h0;  // 重置payload计数器

                // 重置写入计数器（为下一次命令准备）
                if (cmd == CMD_SEQ_PARALLEL_MODE) begin
                    parallel_write_count <= 8'h0;
                end

                case (cmd)
                    CMD_SEQ_START: begin
                        seq_enable <= 1'b1;
                    end
                    CMD_SEQ_STOP: begin
                        seq_enable <= 1'b0;
                        channel_mask <= 8'h0;  // 🔧 清零通道掩码，允许重新配置
                    end
                    // 新协议：使能控制
                    CMD_SEQ_ENABLE_CONTROL: begin
                        seq_enable <= (channel_mask != 8'h0);  // 如果有通道使能则启动
                        mode_parallel <= 1'b0;  // 新协议默认串行模式
                    end
                    // 新协议：全局复位
                    CMD_SEQ_RESET_ALL: begin
                        seq_enable <= 1'b0;
                        channel_mask <= 8'h0;
                        state <= S_IDLE;
                    end
                endcase
            end

            // ========== 新协议：配置通道参数 (0x40) ==========
            // payload: [通道ID][频率字31:24][23:16][15:8][7:0][长度]（6字节）
            if (payload_valid && cmd == CMD_SEQ_CONFIG_CHANNEL) begin
                if (!cmd_latched) begin
                    // 第一个字节：通道ID
                    new_channel_id <= payload_data[2:0];
                    payload_counter <= 16'd1;
                    new_freq_byte_cnt <= 3'd0;
                    cmd_latched <= 1'b1;
                end
                else begin
                    case (payload_counter)
                        16'd1: begin
                            // 第二个字节：频率字[31:24]（最高字节）
                            new_freq_word[31:24] <= payload_data;
                            payload_counter <= 16'd2;
                        end
                        16'd2: begin
                            // 第三个字节：频率字[23:16]
                            new_freq_word[23:16] <= payload_data;
                            payload_counter <= 16'd3;
                        end
                        16'd3: begin
                            // 第四个字节：频率字[15:8]
                            new_freq_word[15:8] <= payload_data;
                            payload_counter <= 16'd4;
                        end
                        16'd4: begin
                            // 第五个字节：频率字[7:0]（最低字节）
                            new_freq_word[7:0] <= payload_data;
                            // 立即保存32位频率字
                            freq_word_array[new_channel_id] <= {new_freq_word[31:8], payload_data};
                            payload_counter <= 16'd5;
                        end
                        16'd5: begin
                            // 第六个字节：序列长度
                            serial_len_array[new_channel_id] <= payload_data;
                            payload_counter <= 16'd0;  // 重置计数器
                            // 不在这里清除cmd_latched，等cmd_done
                        end
                        default: begin
                            payload_counter <= 16'd0;
                        end
                    endcase
                end
            end

            // ========== 新协议：写入序列数据 (0x41) ==========
            // payload: [通道ID][地址][数据]
            if (payload_valid && cmd == CMD_SEQ_WRITE_DATA) begin
                if (!cmd_latched) begin
                    // 第一个字节：通道ID
                    new_channel_id <= payload_data[2:0];
                    payload_counter <= 16'd1;
                    cmd_latched <= 1'b1;
                end
                else begin
                    case (payload_counter)
                        16'd1: begin
                            // 第二个字节：地址
                            new_wr_addr <= payload_data;
                            payload_counter <= 16'd2;
                        end
                        16'd2: begin
                            // 第三个字节：数据
                            // 写入到对应通道的RAM（字节地址）
                            if (new_wr_addr < 8'd32) begin
                                serial_ram[new_channel_id][new_wr_addr] <= payload_data;
                            end
                            payload_counter <= 16'd0;  // 重置计数器
                            // 不在这里清除cmd_latched，等cmd_done
                        end
                        default: begin
                            payload_counter <= 16'd0;
                        end
                    endcase
                end
            end

            // ========== 新协议：使能控制 (0x42) ==========
            // payload: [使能掩码]
            if (payload_valid && cmd == CMD_SEQ_ENABLE_CONTROL && !cmd_latched) begin
                channel_mask <= payload_data;
                cmd_latched <= 1'b1;
            end

            case (state)
                //-------------------------------------------------------------
                // 空闲状态：用于频率控制和串行模式的多字节接收
                //-------------------------------------------------------------
                S_IDLE: begin
                    // 频率控制：在收到第一个payload时进入接收状态
                    if (payload_valid && !cmd_latched && cmd == CMD_SEQ_FREQ_CONTROL) begin
                        cmd_reg <= cmd;
                        cmd_latched <= 1'b1;
                        payload_counter <= 16'd1;  // 第一个字节
                        freq_buffer[7:0] <= payload_data;
                        state <= S_RECV_FREQ;
                    end

                    // 串行模式：第一个字节是通道ID（不是掩码）
                    else if (payload_valid && !cmd_latched && cmd == CMD_SEQ_SERIAL_MODE) begin
                        cmd_reg <= cmd;
                        cmd_latched <= 1'b1;
                        payload_counter <= 16'h0;

                        // payload_data现在是通道ID (0-7)，不是掩码
                        serial_channel_id <= payload_data[2:0];  // 提取低3位作为通道ID
                        channel_mask <= channel_mask | (8'h01 << payload_data[2:0]);  // 🔧 累加掩码
                        mode_parallel <= 1'b0;

                        state <= S_RECV_SER_LEN;
                    end

                    // 帧结束时清除锁存标志（对于已经处理完的命令）
                    else if (cmd_done && cmd_latched) begin
                        state <= S_IDLE;
                    end
                end

                //-------------------------------------------------------------
                // 串行模式：接收序列长度
                //-------------------------------------------------------------
                S_RECV_SER_LEN: begin
                    if (payload_valid) begin
                        serial_seq_len <= payload_data;
                        serial_len_array[serial_channel_id] <= payload_data;  // 🔧 保存到对应通道
                        seq_length <= payload_data;  // 也保存到seq_length
                        serial_wr_addr <= 8'h0;
                        serial_byte_count <= 8'h0;
                        payload_counter <= 16'h0;
                        state <= S_RECV_SER_DATA;
                    end
                end

                //-------------------------------------------------------------
                // 串行模式：接收比特序列（按字节打包传输）
                // 优化版：直接按字节存储，减少位访问复杂度
                //-------------------------------------------------------------
                S_RECV_SER_DATA: begin
                    if (payload_valid) begin
                        // 直接写入字节到RAM
                        serial_ram[serial_channel_id][serial_byte_count] <= payload_data;

                        serial_wr_en <= 1'b1;
                        serial_byte_count <= serial_byte_count + 8'd1;

                        // 固定接收32字节（256位）
                        if (serial_byte_count == 8'd31) begin
                            state <= S_IDLE;
                        end
                    end
                end

                //-------------------------------------------------------------
                // 频率控制：接收4字节频率字（小端序）
                //-------------------------------------------------------------
                S_RECV_FREQ: begin
                    if (payload_valid) begin
                        case (payload_counter)
                            16'd1:
                                freq_buffer[15:8]  <= payload_data;
                            16'd2:
                                freq_buffer[23:16] <= payload_data;
                            16'd3: begin
                                freq_buffer[31:24] <= payload_data;
                                freq_word <= {payload_data, freq_buffer[23:0]};
                                state <= S_IDLE;
                            end
                        endcase
                        payload_counter <= payload_counter + 1;
                    end
                end

                default:
                    state <= S_IDLE;
            endcase
        end
    end

    //=========================================================================
    // 序列播放引擎
    //=========================================================================
    wire [7:0] parallel_seq_out;
    wire [7:0] serial_seq_out;
    wire [7:0] parallel_rd_addr;

    // 将字节数组展平成256位（用于传递给串行引擎）
    wire [255:0] serial_ram_flat_0;
    wire [255:0] serial_ram_flat_1;
    wire [255:0] serial_ram_flat_2;
    wire [255:0] serial_ram_flat_3;
    wire [255:0] serial_ram_flat_4;
    wire [255:0] serial_ram_flat_5;
    wire [255:0] serial_ram_flat_6;
    wire [255:0] serial_ram_flat_7;

    assign serial_ram_flat_0 = {serial_ram[0][31], serial_ram[0][30], serial_ram[0][29], serial_ram[0][28],
                                serial_ram[0][27], serial_ram[0][26], serial_ram[0][25], serial_ram[0][24],
                                serial_ram[0][23], serial_ram[0][22], serial_ram[0][21], serial_ram[0][20],
                                serial_ram[0][19], serial_ram[0][18], serial_ram[0][17], serial_ram[0][16],
                                serial_ram[0][15], serial_ram[0][14], serial_ram[0][13], serial_ram[0][12],
                                serial_ram[0][11], serial_ram[0][10], serial_ram[0][9],  serial_ram[0][8],
                                serial_ram[0][7],  serial_ram[0][6],  serial_ram[0][5],  serial_ram[0][4],
                                serial_ram[0][3],  serial_ram[0][2],  serial_ram[0][1],  serial_ram[0][0]};

    assign serial_ram_flat_1 = {serial_ram[1][31], serial_ram[1][30], serial_ram[1][29], serial_ram[1][28],
                                serial_ram[1][27], serial_ram[1][26], serial_ram[1][25], serial_ram[1][24],
                                serial_ram[1][23], serial_ram[1][22], serial_ram[1][21], serial_ram[1][20],
                                serial_ram[1][19], serial_ram[1][18], serial_ram[1][17], serial_ram[1][16],
                                serial_ram[1][15], serial_ram[1][14], serial_ram[1][13], serial_ram[1][12],
                                serial_ram[1][11], serial_ram[1][10], serial_ram[1][9],  serial_ram[1][8],
                                serial_ram[1][7],  serial_ram[1][6],  serial_ram[1][5],  serial_ram[1][4],
                                serial_ram[1][3],  serial_ram[1][2],  serial_ram[1][1],  serial_ram[1][0]};

    assign serial_ram_flat_2 = {serial_ram[2][31], serial_ram[2][30], serial_ram[2][29], serial_ram[2][28],
                                serial_ram[2][27], serial_ram[2][26], serial_ram[2][25], serial_ram[2][24],
                                serial_ram[2][23], serial_ram[2][22], serial_ram[2][21], serial_ram[2][20],
                                serial_ram[2][19], serial_ram[2][18], serial_ram[2][17], serial_ram[2][16],
                                serial_ram[2][15], serial_ram[2][14], serial_ram[2][13], serial_ram[2][12],
                                serial_ram[2][11], serial_ram[2][10], serial_ram[2][9],  serial_ram[2][8],
                                serial_ram[2][7],  serial_ram[2][6],  serial_ram[2][5],  serial_ram[2][4],
                                serial_ram[2][3],  serial_ram[2][2],  serial_ram[2][1],  serial_ram[2][0]};

    assign serial_ram_flat_3 = {serial_ram[3][31], serial_ram[3][30], serial_ram[3][29], serial_ram[3][28],
                                serial_ram[3][27], serial_ram[3][26], serial_ram[3][25], serial_ram[3][24],
                                serial_ram[3][23], serial_ram[3][22], serial_ram[3][21], serial_ram[3][20],
                                serial_ram[3][19], serial_ram[3][18], serial_ram[3][17], serial_ram[3][16],
                                serial_ram[3][15], serial_ram[3][14], serial_ram[3][13], serial_ram[3][12],
                                serial_ram[3][11], serial_ram[3][10], serial_ram[3][9],  serial_ram[3][8],
                                serial_ram[3][7],  serial_ram[3][6],  serial_ram[3][5],  serial_ram[3][4],
                                serial_ram[3][3],  serial_ram[3][2],  serial_ram[3][1],  serial_ram[3][0]};

    assign serial_ram_flat_4 = {serial_ram[4][31], serial_ram[4][30], serial_ram[4][29], serial_ram[4][28],
                                serial_ram[4][27], serial_ram[4][26], serial_ram[4][25], serial_ram[4][24],
                                serial_ram[4][23], serial_ram[4][22], serial_ram[4][21], serial_ram[4][20],
                                serial_ram[4][19], serial_ram[4][18], serial_ram[4][17], serial_ram[4][16],
                                serial_ram[4][15], serial_ram[4][14], serial_ram[4][13], serial_ram[4][12],
                                serial_ram[4][11], serial_ram[4][10], serial_ram[4][9],  serial_ram[4][8],
                                serial_ram[4][7],  serial_ram[4][6],  serial_ram[4][5],  serial_ram[4][4],
                                serial_ram[4][3],  serial_ram[4][2],  serial_ram[4][1],  serial_ram[4][0]};

    assign serial_ram_flat_5 = {serial_ram[5][31], serial_ram[5][30], serial_ram[5][29], serial_ram[5][28],
                                serial_ram[5][27], serial_ram[5][26], serial_ram[5][25], serial_ram[5][24],
                                serial_ram[5][23], serial_ram[5][22], serial_ram[5][21], serial_ram[5][20],
                                serial_ram[5][19], serial_ram[5][18], serial_ram[5][17], serial_ram[5][16],
                                serial_ram[5][15], serial_ram[5][14], serial_ram[5][13], serial_ram[5][12],
                                serial_ram[5][11], serial_ram[5][10], serial_ram[5][9],  serial_ram[5][8],
                                serial_ram[5][7],  serial_ram[5][6],  serial_ram[5][5],  serial_ram[5][4],
                                serial_ram[5][3],  serial_ram[5][2],  serial_ram[5][1],  serial_ram[5][0]};

    assign serial_ram_flat_6 = {serial_ram[6][31], serial_ram[6][30], serial_ram[6][29], serial_ram[6][28],
                                serial_ram[6][27], serial_ram[6][26], serial_ram[6][25], serial_ram[6][24],
                                serial_ram[6][23], serial_ram[6][22], serial_ram[6][21], serial_ram[6][20],
                                serial_ram[6][19], serial_ram[6][18], serial_ram[6][17], serial_ram[6][16],
                                serial_ram[6][15], serial_ram[6][14], serial_ram[6][13], serial_ram[6][12],
                                serial_ram[6][11], serial_ram[6][10], serial_ram[6][9],  serial_ram[6][8],
                                serial_ram[6][7],  serial_ram[6][6],  serial_ram[6][5],  serial_ram[6][4],
                                serial_ram[6][3],  serial_ram[6][2],  serial_ram[6][1],  serial_ram[6][0]};

    assign serial_ram_flat_7 = {serial_ram[7][31], serial_ram[7][30], serial_ram[7][29], serial_ram[7][28],
                                serial_ram[7][27], serial_ram[7][26], serial_ram[7][25], serial_ram[7][24],
                                serial_ram[7][23], serial_ram[7][22], serial_ram[7][21], serial_ram[7][20],
                                serial_ram[7][19], serial_ram[7][18], serial_ram[7][17], serial_ram[7][16],
                                serial_ram[7][15], serial_ram[7][14], serial_ram[7][13], serial_ram[7][12],
                                serial_ram[7][11], serial_ram[7][10], serial_ram[7][9],  serial_ram[7][8],
                                serial_ram[7][7],  serial_ram[7][6],  serial_ram[7][5],  serial_ram[7][4],
                                serial_ram[7][3],  serial_ram[7][2],  serial_ram[7][1],  serial_ram[7][0]};

    // 并行模式播放引擎
    sequence_playback_parallel parallel_engine(
                                   .clk            (clk),
                                   .rst_n          (rst_n),
                                   .enable         (seq_enable & mode_parallel),
                                   .seq_length     (seq_length),
                                   .freq_word      (freq_word),
                                   .ram_data       (parallel_ram[parallel_rd_addr]),
                                   .rd_addr        (parallel_rd_addr),
                                   .seq_out        (parallel_seq_out)
                               );

    // 串行模式播放引擎（8通道独立频率 - 使用V3版本32位DDS）
    sequence_playback_serial_v3 serial_engine_v3(
                                    .clk            (clk),
                                    .rst_n          (rst_n),
                                    .enable         (seq_enable & ~mode_parallel),
                                    .channel_mask   (channel_mask),
                                    .seq_len_ch0    (serial_len_array[0]),
                                    .seq_len_ch1    (serial_len_array[1]),
                                    .seq_len_ch2    (serial_len_array[2]),
                                    .seq_len_ch3    (serial_len_array[3]),
                                    .seq_len_ch4    (serial_len_array[4]),
                                    .seq_len_ch5    (serial_len_array[5]),
                                    .seq_len_ch6    (serial_len_array[6]),
                                    .seq_len_ch7    (serial_len_array[7]),
                                    .freq_word_ch0  (freq_word_array[0]),
                                    .freq_word_ch1  (freq_word_array[1]),
                                    .freq_word_ch2  (freq_word_array[2]),
                                    .freq_word_ch3  (freq_word_array[3]),
                                    .freq_word_ch4  (freq_word_array[4]),
                                    .freq_word_ch5  (freq_word_array[5]),
                                    .freq_word_ch6  (freq_word_array[6]),
                                    .freq_word_ch7  (freq_word_array[7]),
                                    .serial_ram_0   (serial_ram_flat_0),
                                    .serial_ram_1   (serial_ram_flat_1),
                                    .serial_ram_2   (serial_ram_flat_2),
                                    .serial_ram_3   (serial_ram_flat_3),
                                    .serial_ram_4   (serial_ram_flat_4),
                                    .serial_ram_5   (serial_ram_flat_5),
                                    .serial_ram_6   (serial_ram_flat_6),
                                    .serial_ram_7   (serial_ram_flat_7),
                                    .seq_out        (serial_seq_out)
                                );

    // 输出选择
    assign seq_output = mode_parallel ? parallel_seq_out : serial_seq_out;

    // 状态输出
    assign status = {
               mode_parallel,      // bit[7]: 1=并行, 0=串行
               seq_enable,         // bit[6]: 输出使能
               3'b0,               // bit[5:3]: 预留
               serial_channel_id   // bit[2:0]: 当前通道ID
           };

endmodule

