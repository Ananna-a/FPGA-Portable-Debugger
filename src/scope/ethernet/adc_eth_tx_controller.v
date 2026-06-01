//=============================================================================
// ADC以太网发送控制器
// 功能：将ADC采样数据通过UDP/IP/Ethernet发送到PC
// 版本：V7.0 - 添加协议帧头，根治相位翻转问题
// 日期：2025-11-21
// 说明：
//   - 从异步FIFO读取ADC数据（50MHz域写入，125MHz域读出）
//   - 添加16字节协议头 (帧头+包序号+相位标志+通道使能)
//   - 发送1008字节ADC数据 (504对样本，偶数对)
//   - 总包大小：1024字节 (16头+1008数据)
//   - 监控adc_stream_active状态（CDC同步）
//   - 自动触发eth_udp_tx_gmii模块发送
//
// 协议格式：
//   [0-1]   帧头: 0x5A 0xAA
//   [2-3]   包序号: 16位大端序
//   [4]     标志: Bit0=相位标志(0=CH1首,1=CH2首)
//   [5]     通道使能: Bit0=CH1, Bit1=CH2
//   [6-15]  保留字节
//   [16-1023] ADC数据: 1008字节 = 504对样本
//=============================================================================

module adc_eth_tx_controller (
        input  wire         clk125M,            // 125MHz以太网时钟
        input  wire         rst_n,              // 复位信号（低电平有效）

        // ADC数据FIFO接口（125MHz域）
        input  wire [7:0]   fifo_data,          // FIFO输出数据
        input  wire         fifo_empty,         // FIFO空标志
        input  wire [15:0]  fifo_data_count,    // FIFO数据计数（16位，支持0-65535）🔥 V7.2扩展到16位
        output reg          fifo_rd_en,         // FIFO读使能

        // 以太网UDP模块接口
        output reg          tx_en_pulse,        // 发送触发脉冲（单周期）
        input  wire         tx_done,            // 发送完成标志
        input  wire         payload_req,        // 负载数据请求
        output reg  [7:0]   payload_data,       // 负载数据输出

        // 控制接口（来自50MHz域，需要CDC同步）
        input  wire         adc_stream_active,  // ADC流模式激活标志（异步输入）
        input  wire         ch1_enable,         // CH1使能（异步输入）
        input  wire         ch2_enable,         // CH2使能（异步输入）

        // 🔥 V8.7.10新增：Buffer模式控制
        input  wire         adc_mode,           // 0=Stream流模式, 1=Buffer模式
        input  wire [31:0]  total_samples,      // Buffer模式总采样点数（来自ddr3_write_count）

        // 调试接口
        output reg  [31:0]  packet_count,       // 已发送包计数
        output reg  [2:0]   state_debug         // 状态机调试输出
    );

    //=========================================================================
    // 参数定义
    //=========================================================================
    // 🔥 V8.7.24修改: 两种模式都使用协议头
    parameter HEADER_SIZE = 16'd16;         // 协议头大小：16字节 (两种模式通用)
    parameter STREAM_PACKET_SIZE = 16'd1024; // 流模式包大小：16头+1008数据=1024字节 (504点)
    parameter BUFFER_PACKET_SIZE = 16'd1040; // Buffer模式包大小：16头+1024数据=1040字节 (512点)
    parameter STREAM_DATA_SIZE   = 16'd1008; // 流模式数据：1008字节
    parameter BUFFER_DATA_SIZE   = 16'd1024; // Buffer模式数据：1024字节

    // 状态机定义
    localparam IDLE          = 3'd0;   // 空闲状态
    localparam WAIT_DATA     = 3'd1;   // 等待FIFO有足够数据
    localparam TRIGGER_TX    = 3'd2;   // 触发发送
    localparam SEND_HEADER   = 3'd3;   // 🔥 V7.0: 发送协议头
    localparam SEND_DATA     = 3'd4;   // 发送ADC数据
    localparam WAIT_TX_DONE  = 3'd5;   // 等待发送完成    //=========================================================================
    // CDC同步器：adc_stream_active + ch1_enable + ch2_enable + adc_mode + total_samples（50MHz → 125MHz）
    //=========================================================================
    reg adc_active_sync1, adc_active_sync2;
    reg ch1_en_sync1, ch1_en_sync2;
    reg ch2_en_sync1, ch2_en_sync2;
    reg mode_sync1, mode_sync2;  // 🔥 新增：adc_mode同步
    reg [31:0] samples_sync1, samples_sync2;  // 🔥 新增：total_samples同步

    always @(posedge clk125M or negedge rst_n) begin
        if (!rst_n) begin
            adc_active_sync1 <= 1'b0;
            adc_active_sync2 <= 1'b0;
            ch1_en_sync1 <= 1'b0;
            ch1_en_sync2 <= 1'b0;
            ch2_en_sync1 <= 1'b0;
            ch2_en_sync2 <= 1'b0;
            mode_sync1 <= 1'b0;
            mode_sync2 <= 1'b0;
            samples_sync1 <= 32'd0;
            samples_sync2 <= 32'd0;
        end
        else begin
            adc_active_sync1 <= adc_stream_active;
            adc_active_sync2 <= adc_active_sync1;
            ch1_en_sync1 <= ch1_enable;
            ch1_en_sync2 <= ch1_en_sync1;
            ch2_en_sync1 <= ch2_enable;
            ch2_en_sync2 <= ch2_en_sync1;
            mode_sync1 <= adc_mode;
            mode_sync2 <= mode_sync1;
            samples_sync1 <= total_samples;
            samples_sync2 <= samples_sync1;
        end
    end

    wire adc_active = adc_active_sync2;
    wire ch1_en = ch1_en_sync2;
    wire ch2_en = ch2_en_sync2;
    wire mode_buffer = mode_sync2;            // 🔥 1=Buffer模式
    wire [31:0] total_samp = samples_sync2;   // 🔥 总采样点数

    //=========================================================================
    // V8.7.30: Buffer模式总包数计算（修正单双通道误解）
    //=========================================================================
    // 🔥 V8.7.30关键修正: 不论单双通道，DDR3存储的都是16位数据
    //   包格式: 16字节头 + 1024字节数据 = 1040字节总包大小
    //
    // 🎯 数据流真相:
    //   1. DDR3存储: 16位数据 [CH2, CH1] (不论单双通道都是16位)
    //   2. 单通道interleaver重复:
    //      - 单CH1: {CH1, CH1} → DDR3存[CH1, CH1]
    //      - 单CH2: {CH2, CH2} → DDR3存[CH2, CH2]
    //   3. ddr3_write_count = DDR3写入的16位数据个数
    //   4. 以太网字节数 = ddr3_write_count * 2 (16位→2个8位字节)
    //
    // ✅ 正确计算公式 (不论单双通道):
    //   total_bytes = ddr3_write_count * 2
    //   total_packets = total_bytes / 1024 = (ddr3_write_count * 2) >> 10
    //
    // 示例:
    //   采样1024点 → ddr3_write_count=1024 → 2048字节 → 2包 (单双通道相同!)
    //   采样10240点 → ddr3_write_count=10240 → 20480字节 → 20包
    reg [31:0] total_packets;  // Buffer模式总包数
    reg [31:0] current_packet; // Buffer模式当前包号（0开始）
    reg total_packets_calculated; // 🔥 新增：标志位，防止重复计算
    reg adc_active_d1; // 🔥 V8.7.13: 简化为单级延迟，仅用于状态检测
    reg [10:0] last_packet_bytes; // 🔥 V8.7.41: 最后一包的实际字节数 (1-1024)

    always @(posedge clk125M or negedge rst_n) begin
        if (!rst_n) begin
            total_packets <= 32'd0;
            total_packets_calculated <= 1'b0;
            adc_active_d1 <= 1'b0;
            last_packet_bytes <= 11'd0;
        end
        else begin
            adc_active_d1 <= adc_active;

            // 🔥 V8.7.35修复: IDLE状态彻底清除Buffer模式相关标志
            if (state == IDLE) begin
                // IDLE状态清除标志，准备下次采集
                total_packets_calculated <= 1'b0;
                total_packets <= 32'd0;
                last_packet_bytes <= 11'd0;
            end
            // 🔥 V8.7.53修复: 多包策略 - FPGA多发1包给上位机做冗余保护
            // 策略：FPGA发送(理论包数+1)包，上位机只处理前N包，丢弃最后1包
            // 原因：最后一包可能包含DDR3未初始化数据或16→8位转换器残留数据
            //
            // 原公式: total_packets = ceil((total_samp * 2) / 1024)
            //   = ceil(total_samp / 512)
            //   = (total_samp + 511) >> 9
            //
            // V8.7.53新公式: total_packets = ceil(total_samp / 512) + 1
            //   = ((total_samp + 511) >> 9) + 1
            //
            // 示例: total_samp=1024
            //   理论: (1024+511)>>9 = 2包
            //   实际发送: 2+1 = 3包
            //   上位机接收: 3包，处理前2包，丢弃第3包
            else if (mode_buffer && !total_packets_calculated && adc_active) begin
                // ✅ 向上取整 + 1包冗余
                total_packets <= ((total_samp + 32'd511) >> 9) + 32'd1;

                // ✅ 计算最后一包字节数: (total_samp * 2) % 1024
                // 如果余数为0，说明正好整除，最后一包是1024字节
                last_packet_bytes <= ((total_samp << 1) & 32'h3FF) == 32'd0 ? 11'd1024 : ((total_samp << 1) & 32'h3FF);

                total_packets_calculated <= 1'b1;
            end
        end
    end

    //=========================================================================
    // V7.0/V8.7.10: 协议头生成逻辑
    //=========================================================================
    reg [15:0] packet_seq;        // 包序号 (0-65535循环)
    reg [4:0]  header_byte_cnt;   // 协议头字节计数器 (0-15)
    reg [7:0]  header_data;       // 当前协议头字节
    wire       is_last_packet;    // 🔥 最后一包标志（Buffer模式使用）

    // 🔥 V8.7.30修复: is_last_packet判断逻辑
    // current_packet表示"已发送包数"（在WAIT_TX_DONE递增）
    // 🔥 V8.7.37修复: current_packet在TRIGGER_TX时已递增，直接比较
    // 例: total_packets=2
    //   发第1包: TRIGGER_TX时current_packet变1, 1 != 2, 不是最后包
    //   发第2包: TRIGGER_TX时current_packet变2, 2 == 2, 是最后包 ✅
    assign is_last_packet = mode_buffer && (current_packet == total_packets);

    // 🔥 V8.7.34修复: 包序号递增和清除
    always @(posedge clk125M or negedge rst_n) begin
        if (!rst_n) begin
            packet_seq <= 16'd0;
        end
        else if (state == IDLE) begin
            // 🔥 V8.7.34修复: IDLE状态无条件清零，支持多次采集
            packet_seq <= 16'd0;
        end
        else if (state == WAIT_TX_DONE && tx_done_posedge) begin
            packet_seq <= packet_seq + 1'b1;  // 每发完一包，序号+1
        end
    end

    // 🔥 V8.7.37修复: current_packet语义改为"将要发送的包号"（1-based）
    // 递增时机: TRIGGER_TX状态，进入发送前+1
    // 初始值: 0
    // 判断: current_packet == total_packets 表示正在发送最后一包
    //       current_packet > total_packets 表示已发完所有包

    always @(posedge clk125M or negedge rst_n) begin
        if (!rst_n) begin
            current_packet <= 32'd0;
        end
        else begin
            if (state == IDLE) begin
                current_packet <= 32'd0;
            end
            else if (state == TRIGGER_TX && mode_buffer) begin
                // 🔥 关键修复：进入发送前递增，协议头中使用正确的包号
                current_packet <= current_packet + 1'b1;
            end
        end
    end

    // 🔥 V8.7.37: current_packet已经是1-based，直接使用
    wire [31:0] current_packet_1based;
    assign current_packet_1based = current_packet;

    // 协议头数据生成 (组合逻辑)
    always @(*) begin
        case (header_byte_cnt)
            5'd0:
                header_data = 8'h5A;                  // 帧头1
            5'd1:
                header_data = 8'hAA;                  // 帧头2
            5'd2:
                header_data = packet_seq
                [15:8];       // 包序号高字节
            5'd3:
                header_data = packet_seq[7:0];        // 包序号低字节
            5'd4:
                header_data = {6'b0, is_last_packet, 1'b0}; // 🔥 Bit1=最后一包, Bit0=相位(固定0)
            5'd5:
                header_data = {6'b0, ch2_en, ch1_en}; // 通道使能
            5'd6:
                header_data = total_packets[15:8];    // 🔥 总包数高字节
            5'd7:
                header_data = total_packets[7:0];     // 🔥 总包数低字节
            5'd8:
                header_data = current_packet_1based[15:8];   // 🔥 当前包号高字节(1-based)
            5'd9:
                header_data = current_packet_1based[7:0];    // 🔥 当前包号低字节(1-based)
            default:
                header_data = 8'h00;                  // 保留字节 (10-15)
        endcase
    end

    //=========================================================================
    // 状态机
    //=========================================================================
    reg [2:0] state;
    reg [15:0] byte_cnt;        // 当前包字节计数
    reg tx_done_d1;             // tx_done延迟一拍（边沿检测）
    wire tx_done_posedge;
    reg [15:0] wait_timeout_cnt;  // 🔥 V8.7.40: FIFO等待超时计数器

    assign tx_done_posedge = tx_done && (!tx_done_d1);

    always @(posedge clk125M or negedge rst_n) begin
        if (!rst_n) begin
            tx_done_d1 <= 1'b0;
        end
        else begin
            tx_done_d1 <= tx_done;
        end
    end

    always @(posedge clk125M or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            tx_en_pulse <= 1'b0;
            fifo_rd_en <= 1'b0;
            payload_data <= 8'd0;
            byte_cnt <= 16'd0;
            packet_count <= 32'd0;
            state_debug <= 3'd0;
            header_byte_cnt <= 5'd0;
            wait_timeout_cnt <= 16'd0;  // 🔥 V8.7.40: 初始化超时计数器
        end
        else begin
            case (state)
                //-------------------------------------------------------------
                // IDLE: 等待ADC采集激活
                //-------------------------------------------------------------
                IDLE: begin
                    tx_en_pulse <= 1'b0;
                    fifo_rd_en <= 1'b0;
                    byte_cnt <= 16'd0;
                    header_byte_cnt <= 5'd0;
                    payload_data <= 8'h5A;    // 🔥 预设帧头，防止UDP模块读到垃圾数据
                    state_debug <= 3'd0;
                    packet_count <= 32'd0;    // 🔥 V8.7.34修复: IDLE状态清零包计数，支持多次采集
                    wait_timeout_cnt <= 16'd0;  // 🔥 V8.7.40: 清零超时计数器

                    if (adc_active && !fifo_empty) begin
                        state <= WAIT_DATA;
                    end
                end

                //-------------------------------------------------------------
                // WAIT_DATA: 等待FIFO累积足够数据
                //-------------------------------------------------------------
                WAIT_DATA: begin
                    tx_en_pulse <= 1'b0;
                    state_debug <= 3'd1;

                    // 🔥 V8.7.30修复: 两种模式都使用协议头
                    if (mode_buffer) begin
                        // Buffer模式：发送协议头 + 1024字节数据
                        fifo_rd_en <= 1'b0;
                        payload_data <= 8'h5A;    // 🔥 预设帧头5A（与流模式相同）

                        // 🔥 V8.7.39修复: Buffer模式不检查adc_active，只检查包数和FIFO
                        // 原因：DDR3读完后eth_transfer_active会立即拉低，但FIFO里还有数据！
                        // 应该继续发送直到达到total_packets或FIFO真的空了

                        // 🔥 V8.7.40: 添加超时保护（避免FIFO一直没数据导致卡死）
                        // 超时时间: 65535 × 8ns = 0.5ms @ 125MHz
                        if (!adc_active && fifo_empty) begin
                            // DDR3传输完成 + FIFO空 → 递增超时计数器
                            wait_timeout_cnt <= wait_timeout_cnt + 1'b1;
                            if (wait_timeout_cnt >= 16'd65535) begin
                                // 超时 → 强制回IDLE
                                state <= IDLE;
                            end
                        end
                        else begin
                            // FIFO有数据或DDR3还在传输 → 清零超时计数器
                            wait_timeout_cnt <= 16'd0;
                        end

                        // 🔥 检查是否已发完所有包
                        if (total_packets_calculated && current_packet >= total_packets) begin
                            // ✅ 已发完所有包（current_packet=2 >= total_packets=2）
                            state <= IDLE;
                        end
                        else if (total_packets_calculated && (current_packet < total_packets)) begin
                            // ✅ 还有包要发（current_packet < total_packets）
                            if ((current_packet + 1 == total_packets) && !fifo_empty) begin
                                // 🔥 下一包是最后一包：FIFO不空就发（不要求1024字节）
                                state <= TRIGGER_TX;
                            end
                            else if (fifo_data_count >= BUFFER_DATA_SIZE) begin
                                // ✅ 非最后一包：必须等够1024字节
                                state <= TRIGGER_TX;
                            end
                            // 否则继续等待FIFO累积数据
                        end
                        else if (!total_packets_calculated) begin
                            // ⚠️ 还未计算total_packets，继续等待
                        end
                    end
                    else begin
                        // 流模式：有协议头，预设帧头
                        fifo_rd_en <= 1'b0;
                        payload_data <= 8'h5A;    // 🔥 预设第0字节(5A)

                        if (fifo_data_count >= STREAM_DATA_SIZE) begin
                            state <= TRIGGER_TX;
                        end
                        else if (!adc_active && !fifo_empty) begin
                            // ADC停止，发送剩余数据
                            state <= TRIGGER_TX;
                        end
                        else if (!adc_active && fifo_empty) begin
                            // ADC停止且FIFO空，返回IDLE
                            state <= IDLE;
                        end
                    end
                end

                //-------------------------------------------------------------
                // TRIGGER_TX: 触发以太网发送模块
                //-------------------------------------------------------------
                TRIGGER_TX: begin
                    tx_en_pulse <= 1'b1;      // UDP会采样当前payload_data作为第0字节(5A)
                    state_debug <= 3'd2;
                    byte_cnt <= 16'd1;
                    header_byte_cnt <= 5'd1;
                    wait_timeout_cnt <= 16'd0;  // 🔥 V8.7.40: 清零超时计数器

                    // 🔥 V8.7.24修改: 两种模式都发送协议头
                    state <= SEND_HEADER;
                    fifo_rd_en <= 1'b0;
                    // payload_data保持5A（在WAIT_DATA已设置）
                end

                //-------------------------------------------------------------
                // SEND_HEADER: 发送16字节协议头
                //-------------------------------------------------------------
                SEND_HEADER: begin
                    tx_en_pulse <= 1'b0;
                    state_debug <= 3'd3;
                    fifo_rd_en <= 1'b0;       // 🔥 发送协议头时禁止读FIFO

                    if (payload_req) begin
                        // 🔥 V7.0修复: 只在payload_req=1时更新
                        payload_data <= header_data;
                        header_byte_cnt <= header_byte_cnt + 1'b1;
                        byte_cnt <= byte_cnt + 1'b1;

                        // 发送完16字节协议头 (header_byte_cnt从0到15)
                        if (header_byte_cnt == 5'd15) begin
                            state <= SEND_DATA;
                            fifo_rd_en <= 1'b1;  // 🔥 预读第一个ADC字节
                        end
                    end
                    // 🔥 删除else分支，避免重复赋值！
                end

                //-------------------------------------------------------------
                // SEND_DATA: 发送ADC数据
                //-------------------------------------------------------------
                SEND_DATA: begin
                    tx_en_pulse <= 1'b0;
                    state_debug <= 3'd4;

                    if (payload_req) begin
                        // 🔥 V8.7.41修复: Buffer模式最后一包，按实际字节数发送
                        // 原BUG: 最后一包可能不足1024字节，但代码仍读满1024字节，导致读到DDR3随机数据
                        if (fifo_empty && mode_buffer && is_last_packet) begin
                            payload_data <= 8'h00;  // FIFO空时填充0
                            fifo_rd_en <= 1'b0;
                        end
                        else begin
                            // 输出FIFO数据
                            payload_data <= fifo_data;

                            // 🔥 V8.7.41修改: 最后一包按实际字节数判断
                            if (mode_buffer) begin
                                if (is_last_packet) begin
                                    // ✅ 最后一包：按last_packet_bytes判断
                                    // byte_cnt从16开始（协议头），数据部分从16到16+last_packet_bytes-1
                                    if (byte_cnt < 16 + last_packet_bytes - 1'b1 && !fifo_empty) begin
                                        fifo_rd_en <= 1'b1;
                                    end
                                    else begin
                                        fifo_rd_en <= 1'b0;
                                    end
                                end
                                else begin
                                    // 非最后一包：固定1040字节
                                    if (byte_cnt < BUFFER_PACKET_SIZE - 1'b1 && !fifo_empty) begin
                                        fifo_rd_en <= 1'b1;
                                    end
                                    else begin
                                        fifo_rd_en <= 1'b0;
                                    end
                                end
                            end
                            else begin
                                // 流模式：固定1024字节
                                if (byte_cnt < STREAM_PACKET_SIZE - 1'b1 && !fifo_empty) begin
                                    fifo_rd_en <= 1'b1;
                                end
                                else begin
                                    fifo_rd_en <= 1'b0;
                                end
                            end
                        end

                        byte_cnt <= byte_cnt + 1'b1;

                        // 🔥 V8.7.41修改: 最后一包按实际字节数判断结束
                        if (mode_buffer) begin
                            if (is_last_packet) begin
                                // ✅ 最后一包：16头+last_packet_bytes数据
                                if (byte_cnt >= 16 + last_packet_bytes - 1'b1) begin
                                    state <= WAIT_TX_DONE;
                                    fifo_rd_en <= 1'b0;
                                end
                            end
                            else begin
                                // 非最后一包：固定1040字节
                                if (byte_cnt >= BUFFER_PACKET_SIZE - 1'b1) begin
                                    state <= WAIT_TX_DONE;
                                    fifo_rd_en <= 1'b0;
                                end
                            end
                        end
                        else begin
                            // 流模式：固定1024字节
                            if (byte_cnt >= STREAM_PACKET_SIZE - 1'b1) begin
                                state <= WAIT_TX_DONE;
                                fifo_rd_en <= 1'b0;
                            end
                        end
                    end
                end                //-------------------------------------------------------------
                // WAIT_TX_DONE: 等待以太网模块发送完成
                //-------------------------------------------------------------
                WAIT_TX_DONE: begin
                    fifo_rd_en <= 1'b0;
                    state_debug <= 3'd5;

                    if (tx_done_posedge) begin
                        packet_count <= packet_count + 1'b1;

                        // 🔥 V8.7.30修复: current_packet在上方always块已递增
                        if (mode_buffer) begin
                            if (current_packet >= total_packets) begin
                                state <= IDLE;
                            end
                            else begin
                                state <= WAIT_DATA;
                            end
                        end
                        else begin
                            // 流模式：根据adc_active和FIFO状态判断
                            if (adc_active) begin
                                state <= WAIT_DATA;
                            end
                            else if (!fifo_empty) begin
                                // ADC停止但FIFO还有数据，继续发送
                                state <= WAIT_DATA;
                            end
                            else begin
                                // 全部完成，返回IDLE
                                state <= IDLE;
                            end
                        end
                    end
                end

                default: begin
                    state <= IDLE;
                end
            endcase
        end
    end

endmodule
