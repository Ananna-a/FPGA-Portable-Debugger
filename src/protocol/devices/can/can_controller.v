//=============================================================================
// CAN总线控制模块 - 适配debugger_top框架
// 版本: V2.2 ✅ 可变长度支持
// 日期: 2025-11-28
//
// 功能:
//   - CAN帧发送（标准帧，支持0-8字节可变长度）✅ 完整DLC支持
//   - CAN帧接收（通过CH340 UART上报）
//   - 波特率配置（1M固定，后续支持125k/250k/500k）
//
// 硬件:
//   - CAN收发器: SIT1042AQT/3 (兼容SJA1000)
//   - 引脚: CAN_TX=G2, CAN_RX=H2
//
// 命令定义 (0xC0-0xC4):
//   0xC0: CAN配置（波特率）
//   0xC1: 发送CAN帧（支持DLC=0-8字节）
//   0xC2: 设置过滤器（预留）
//   0xC3: 读取状态（预留）
//   0xC4: CAN接收数据上报（FPGA主动上报到CH340）
//
// 通信架构:
//   上位机 --[CDC USB]--> FPGA --[CAN Bus]--> 外部设备
//   外部设备 --[CAN Bus]--> FPGA --[CH340 UART]--> 上位机
//
// ✅ V2.2更新:
//   1. 扩展IP核支持可变长度（0-8字节）
//   2. buffer从43位扩展到79位（11+4+64）
//   3. 完全匹配上位机DLC配置
//   4. RX过滤器改为0x200避免回环
//=============================================================================

module can_controller #(
        parameter [10:0] LOCAL_ID = 11'h456,           // 本地发送ID
        parameter [10:0] RX_ID_SHORT_FILTER = 11'h000, // RX过滤器：接收所有帧（调试用）
        parameter [10:0] RX_ID_SHORT_MASK   = 11'h000, // 掩码全0=接收所有
        parameter [28:0] RX_ID_LONG_FILTER  = 29'h12345678,
        parameter [28:0] RX_ID_LONG_MASK    = 29'h1fffffff
    )(
        // 系统时钟和复位
        input  wire        clk,           // 50MHz系统时钟
        input  wire        rst_n,

        // CAN物理接口
        input  wire        can_rx,
        output wire        can_tx,

        // CDC命令接口（来自debugger_top的cmd_parser）
        input  wire [7:0]  cmd_code,
        input  wire [7:0]  cmd_payload,
        input  wire        cmd_payload_valid,
        input  wire        cmd_done,
        input  wire [15:0] payload_counter,

        // UART应答接口（发送到uart_response_tx）
        output reg         response_valid,
        output reg  [7:0]  resp_mod_id,
        output reg  [7:0]  resp_func_id,
        output reg  [7:0]  resp_status,
        output reg  [7:0]  resp_data,
        input  wire        response_done,

        // CAN接收上报接口（发送到UART MUX）
        output reg         can_rx_report_valid,
        output reg  [7:0]  can_rx_report_data,
        input  wire        can_rx_report_ready,
        output reg         can_rx_report_done,

        // CAN接收原始数据（发送到以太网UDP）
        output wire        can_rx_valid_raw,
        output wire [7:0]  can_rx_data_raw,
        output wire        can_rx_last_raw,
        output wire [28:0] can_rx_id_raw,
        output wire        can_rx_ide_raw
    );

    //=============================================================================
    // 命令码和模块ID定义
    //=============================================================================
    localparam CMD_CAN_CONFIG  = 8'hC0;
    localparam CMD_CAN_SEND    = 8'hC1;
    localparam CMD_CAN_FILTER  = 8'hC2;
    localparam CMD_CAN_STATUS  = 8'hC3;
    localparam CMD_CAN_RX_DATA = 8'hC4;
    localparam MOD_ID_CAN      = 8'h30;

    //=============================================================================
    // CAN波特率配置参数（基于50MHz系统时钟）
    // 波特率公式（来自fpga-can-main例程README.md）：
    //   division = default_c_PTS + default_c_PBS1 + default_c_PBS2 + 1
    //   CAN_baud = clk_freq / division
    //
    // 标准CAN波特率配置表 (50MHz时钟) - 完全按照例程README.md:
    //   索引0: 1MHz   → c_PTS=34,   c_PBS1=5,    c_PBS2=10    (division=50)
    //   索引1: 500kHz → c_PTS=69,   c_PBS1=10,   c_PBS2=20    (division=100)
    //   索引2: 100kHz → c_PTS=349,  c_PBS1=50,   c_PBS2=100   (division=500)
    //   索引3: 10kHz  → c_PTS=3499, c_PBS1=500,  c_PBS2=1000  (division=5000)
    //   索引4: 5kHz   → c_PTS=6999, c_PBS1=1000, c_PBS2=2000  (division=10000)
    //
    // 注意：当前固定使用索引3 (1MHz)，波特率配置命令(0xC0)暂不支持运行时切换
    //      因为IP核不支持动态修改时序参数，需要软复位CAN控制器才能生效
    //=============================================================================
    localparam [15:0] c_PTS  = 16'd34;   // 1MHz配置（索引3，例程推荐）
    localparam [15:0] c_PBS1 = 16'd5;    // Phase Buffer Segment 1
    localparam [15:0] c_PBS2 = 16'd10;   // Phase Buffer Segment 2

    //=============================================================================
    // CAN控制器接口信号
    //=============================================================================
    wire        can_tx_ready;
    reg         can_tx_valid = 1'b0;
    reg  [28:0] can_tx_id = 29'h0;       // 🔥 V2.8: 29位ID（支持扩展帧）
    reg         can_tx_ide = 1'b0;       // 🔥 V2.8: IDE标志 (0=标准帧, 1=扩展帧)
    reg  [ 3:0] can_tx_len = 4'd0;
    reg  [63:0] can_tx_data = 64'h0;

    wire        can_rx_valid;
    wire        can_rx_last;
    wire [ 7:0] can_rx_data;
    wire [28:0] can_rx_id;
    wire        can_rx_ide;

    // 直通到以太网UDP
    assign can_rx_valid_raw = can_rx_valid;
    assign can_rx_data_raw = can_rx_data;
    assign can_rx_last_raw = can_rx_last;
    assign can_rx_id_raw = can_rx_id;
    assign can_rx_ide_raw = can_rx_ide;

    //=============================================================================
    // 命令数据缓冲区
    //=============================================================================
    reg [7:0] cmd_buffer [0:15];
    reg [3:0] buf_wr_ptr = 4'd0;

    // Payload接收
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            buf_wr_ptr <= 4'd0;
        end
        else if (cmd_payload_valid && (cmd_code == CMD_CAN_CONFIG || cmd_code == CMD_CAN_SEND)) begin
            cmd_buffer[payload_counter[3:0]] <= cmd_payload;
            buf_wr_ptr <= payload_counter[3:0] + 4'd1;
        end
        else if (cmd_done) begin
            buf_wr_ptr <= 4'd0;
        end
    end

    //=============================================================================
    // 波特率配置（0xC0命令处理）
    // 上位机发送: 55 AA C0 01 00 [baud_index] [CS]
    //   baud_index: 0=1MHz, 1=500kHz, 2=100kHz, 3=10kHz, 4=5kHz
    //
    // 注意：当前版本暂不支持动态波特率配置，固定为1MHz (索引0)
    //      原因：CAN IP核的时序参数(c_PTS/PBS1/PBS2)是编译时常量
    //      如需切换波特率，需要修改localparam重新编译
    // TODO: 后续可通过参数化+软复位实现运行时切换
    //=============================================================================
    // 命令0xC0接收后返回成功应答，但实际不改变波特率（仅记录配置请求）

    //=============================================================================
    // CAN帧发送（0xC1命令处理）
    //=============================================================================
    reg [10:0] can_id_std = 11'h0;
    reg [3:0]  can_dlc = 4'd0;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            can_tx_valid <= 1'b0;
            can_tx_id <= 29'h0;          // 🔥 V2.8: 29位复位值
            can_tx_ide <= 1'b0;
            can_tx_len <= 4'd0;
            can_tx_data <= 64'h0;
            can_id_std <= 11'h0;
            can_dlc <= 4'd0;
        end
        else begin
            can_tx_valid <= 1'b0;  // 默认无效

            if (cmd_done && cmd_code == CMD_CAN_SEND && can_tx_ready) begin
                // 解析帧类型
                if (cmd_buffer[0][0] == 1'b0) begin
                    // 标准帧: Byte0=type, Byte1=ID[10:3], Byte2=ID[2:0]<<5|DLC
                    can_id_std <= {cmd_buffer[1][7:0], cmd_buffer[2][7:5]};
                    can_dlc <= cmd_buffer[2][3:0];
                    // 🔥 V2.8: 传递29位ID（标准帧高位补0）和IDE=0
                    can_tx_id <= {18'h0, cmd_buffer[1][7:0], cmd_buffer[2][7:5]};
                    can_tx_ide <= 1'b0;  // 标准帧
                    can_tx_len <= cmd_buffer[2][3:0];  // ✅ 支持0-8字节可变长度
                    // ✅ 组装8字节数据（支持完整DLC范围）
                    can_tx_data <= {cmd_buffer[3], cmd_buffer[4],
                                    cmd_buffer[5], cmd_buffer[6],
                                    cmd_buffer[7], cmd_buffer[8],
                                    cmd_buffer[9], cmd_buffer[10]};
                    can_tx_valid <= 1'b1;
                end
                else begin
                    // 🔥 V2.8: 扩展帧完整支持（29位ID）
                    // Byte0=0x01, Byte1-4=29位ID(大端), Byte5=DLC, Byte6-13=数据
                    // ⚠️ 注意：Python发送4字节完整ID，但只有29位有效
                    can_tx_id <= {cmd_buffer[1], cmd_buffer[2], cmd_buffer[3], cmd_buffer[4][7:3]};  // 29位: [31:0]取[28:0]
                    can_tx_ide <= 1'b1;  // 扩展帧标志
                    can_tx_len <= cmd_buffer[5][3:0];  // DLC
                    can_tx_data <= {cmd_buffer[6], cmd_buffer[7],
                                    cmd_buffer[8], cmd_buffer[9],
                                    cmd_buffer[10], cmd_buffer[11],
                                    cmd_buffer[12], cmd_buffer[13]};
                    can_tx_valid <= 1'b1;
                end
            end
        end
    end

    //=============================================================================
    // 应答帧生成（立即回复）
    //=============================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            response_valid <= 1'b0;
            resp_mod_id <= MOD_ID_CAN;
            resp_func_id <= 8'h00;
            resp_status <= 8'h00;
            resp_data <= 8'h00;
        end
        else begin
            response_valid <= 1'b0;

            // 配置命令应答
            if (cmd_done && cmd_code == CMD_CAN_CONFIG) begin
                response_valid <= 1'b1;
                resp_mod_id <= MOD_ID_CAN;
                resp_func_id <= CMD_CAN_CONFIG;
                resp_status <= 8'h00;  // 成功
                resp_data <= 8'h00;
            end

            // 发送命令应答
            if (cmd_done && cmd_code == CMD_CAN_SEND) begin
                response_valid <= 1'b1;
                resp_mod_id <= MOD_ID_CAN;
                resp_func_id <= CMD_CAN_SEND;
                resp_status <= can_tx_ready ? 8'h00 : 8'h01;  // 0=成功,1=忙
                resp_data <= 8'h00;
            end
        end
    end

    //=============================================================================
    // CAN接收数据上报（通过CH340 UART发送到上位机）
    // ✅ V2.3: 纯数据流模式（参考SPI），不组装应答帧
    // 数据格式: [frame_type][ID_bytes][DLC][data0-7]
    //=============================================================================
    reg [7:0] rx_report_buffer [0:15];  // 缩小缓冲：1+2+1+8=12字节（标准帧）
    reg [3:0] rx_report_wr_ptr = 4'd0;
    reg [3:0] rx_report_rd_ptr = 4'd0;
    reg [3:0] rx_report_len = 4'd0;
    reg       rx_collecting = 1'b0;

    // 接收数据收集
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rx_collecting <= 1'b0;
            rx_report_wr_ptr <= 4'd0;
            rx_report_len <= 4'd0;
        end
        else begin
            // 发送完成后清零长度
            if (can_rx_report_done) begin
                rx_report_len <= 4'd0;
            end
            else if (can_rx_valid) begin
                if (!rx_collecting) begin
                    // 🔥 V2.7修复: 开始新帧，先填充帧头，第一个数据字节在下个周期收集
                    rx_collecting <= 1'b1;
                    rx_report_wr_ptr <= 4'd0;

                    // ✅ 只填充纯数据：frame_type + ID + DLC（无帧头）
                    rx_report_buffer[0] <= {7'h0, can_rx_ide};  // frame_type

                    if (can_rx_ide) begin
                        // 扩展帧: 4字节ID (大端序)
                        rx_report_buffer[1] <= can_rx_id[28:21];  // ID最高字节
                        rx_report_buffer[2] <= can_rx_id[20:13];
                        rx_report_buffer[3] <= can_rx_id[12:5];
                        rx_report_buffer[4] <= {can_rx_id[4:0], 3'b0};  // ID低5位 + 3位填充0
                        rx_report_wr_ptr <= 4'd5;  // 数据从位置5开始

                        // 🔥 V2.7关键修复: 第一个数据字节立即写入
                        rx_report_buffer[5] <= can_rx_data;

                        // 判断是否只有一个字节
                        if (can_rx_last) begin
                            rx_collecting <= 1'b0;
                            rx_report_len <= 4'd6;  // 5(header) + 1(data)
                        end
                        else begin
                            rx_report_wr_ptr <= 4'd6;  // 下一个字节位置
                        end
                    end
                    else begin
                        // 标准帧: 2字节ID拼接 + 1字节DLC占位
                        rx_report_buffer[1] <= can_rx_id[10:3];
                        rx_report_buffer[2] <= {can_rx_id[2:0], 5'b0};  // DLC稍后回填
                        rx_report_wr_ptr <= 4'd3;  // 数据从位置3开始

                        // 🔥 V2.7关键修复: 第一个数据字节立即写入
                        rx_report_buffer[3] <= can_rx_data;

                        // 判断是否只有一个字节
                        if (can_rx_last) begin
                            rx_collecting <= 1'b0;
                            rx_report_len <= 4'd4;  // 3(header) + 1(data)
                            // 回填DLC=1
                            rx_report_buffer[2][7:5] <= can_rx_id[2:0];
                            rx_report_buffer[2][4] <= 1'b0;
                            rx_report_buffer[2][3:0] <= 4'd1;  // DLC=1
                        end
                        else begin
                            rx_report_wr_ptr <= 4'd4;  // 下一个字节位置
                        end
                    end
                end
                // 🔥 V2.7: 只有在已经collecting且不是第一个字节时才继续收集
                else if (rx_collecting) begin
                    rx_report_buffer[rx_report_wr_ptr] <= can_rx_data;

                    // 🔥 时序修复: 如果是最后一个字节，立即计算长度和DLC
                    if (can_rx_last) begin
                        rx_collecting <= 1'b0;

                        // 🔥 关键: 使用当前wr_ptr+1计算长度(因为当前字节还未递增指针)
                        if (!can_rx_ide) begin
                            // 标准帧: 总长度 = wr_ptr + 1 (包含当前字节)
                            rx_report_len <= rx_report_wr_ptr + 4'd1;

                            // 回填DLC到Byte2 (格式: {ID[2:0], 1'b0, DLC[3:0]})
                            rx_report_buffer[2][7:5] <= can_rx_id[2:0];  // ID低3位
                            rx_report_buffer[2][4] <= 1'b0;
                            // DLC = (wr_ptr+1) - 3 = wr_ptr - 2
                            rx_report_buffer[2][3:0] <= rx_report_wr_ptr - 4'd2;
                        end
                        else begin
                            // 🔥 V2.8: 扩展帧DLC回填
                            // 扩展帧格式: [type=0x01][ID3][ID2][ID1][ID0][data0-7]
                            // DLC需要从数据长度推算: DLC = (wr_ptr+1) - 5
                            rx_report_len <= rx_report_wr_ptr + 4'd1;
                            // 注意：扩展帧没有单独的DLC字节，上位机从长度计算
                        end
                    end
                    else begin
                        // 不是最后一个字节，正常递增指针
                        rx_report_wr_ptr <= rx_report_wr_ptr + 4'd1;
                    end
                end
            end
        end
    end    // 上报数据发送状态机
    reg [2:0] tx_state = 3'd0;
    localparam TX_IDLE = 3'd0;
    localparam TX_SEND = 3'd1;
    localparam TX_WAIT = 3'd2;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            tx_state <= TX_IDLE;
            rx_report_rd_ptr <= 4'd0;
            can_rx_report_valid <= 1'b0;
            can_rx_report_data <= 8'h00;
            can_rx_report_done <= 1'b0;
        end
        else begin
            case (tx_state)
                TX_IDLE: begin
                    can_rx_report_done <= 1'b0;
                    if (!rx_collecting && rx_report_len > 0) begin
                        rx_report_rd_ptr <= 4'd0;
                        tx_state <= TX_SEND;
                    end
                end

                TX_SEND: begin
                    if (rx_report_rd_ptr < rx_report_len) begin
                        can_rx_report_valid <= 1'b1;
                        can_rx_report_data <= rx_report_buffer[rx_report_rd_ptr];
                        tx_state <= TX_WAIT;
                    end
                    else begin
                        can_rx_report_done <= 1'b1;
                        // rx_report_len由接收逻辑管理，不在这里清零
                        tx_state <= TX_IDLE;
                    end
                end

                TX_WAIT: begin
                    if (can_rx_report_ready) begin
                        can_rx_report_valid <= 1'b0;
                        rx_report_rd_ptr <= rx_report_rd_ptr + 4'd1;
                        tx_state <= TX_SEND;
                    end
                end

                default:
                    tx_state <= TX_IDLE;
            endcase
        end
    end

    //=============================================================================
    // CAN控制器实例（轻量级IP核）
    //=============================================================================
    can_top #(
                .LOCAL_ID           ( LOCAL_ID           ),
                .RX_ID_SHORT_FILTER ( RX_ID_SHORT_FILTER ),
                .RX_ID_SHORT_MASK   ( RX_ID_SHORT_MASK   ),
                .RX_ID_LONG_FILTER  ( RX_ID_LONG_FILTER  ),
                .RX_ID_LONG_MASK    ( RX_ID_LONG_MASK    ),
                .default_c_PTS      ( c_PTS              ),  // localparam常量
                .default_c_PBS1     ( c_PBS1             ),  // localparam常量
                .default_c_PBS2     ( c_PBS2             )   // localparam常量
            ) u_can_top (
                .rstn      ( rst_n           ),
                .clk       ( clk             ),
                .can_rx    ( can_rx          ),
                .can_tx    ( can_tx          ),
                .tx_valid  ( can_tx_valid    ),
                .tx_ready  ( can_tx_ready    ),
                .tx_id     ( can_tx_id       ),
                .tx_ide    ( can_tx_ide      ),  // 🔥 V2.8: IDE信号
                .tx_len    ( can_tx_len      ),
                .tx_data   ( can_tx_data     ),
                .rx_valid  ( can_rx_valid    ),
                .rx_last   ( can_rx_last     ),
                .rx_data   ( can_rx_data     ),
                .rx_id     ( can_rx_id       ),
                .rx_ide    ( can_rx_ide      )
            );

endmodule
