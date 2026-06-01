//=============================================================================
// 蓝牙串口透传桥接模块
// 功能: 实现CDC串口和蓝牙串口之间的双向数据透传
// 数据流向:
//   1. 上位机(CDC) -> FPGA -> 蓝牙 -> 手机
//   2. 手机 -> 蓝牙 -> FPGA -> CH340 -> 上位机
// 说明: 蓝牙模块波特率可通过CMD_UART_CONFIG(0x90)命令配置
//=============================================================================

module bt_uart_bridge(
        input clk,              // 系统时钟 50MHz
        input rst_n,            // 复位信号

        // CDC侧接口 (来自命令解析器的非命令数据)
        input [7:0] cdc_rx_data,    // CDC接收到的数据
        input cdc_rx_valid,          // CDC数据有效（边沿触发）
        output reg cdc_tx_req,       // 请求CDC发送数据(暂不使用,改用CH340)
        output cdc_tx_done,          // CDC数据发送完成（用于多字节发送同步）

        // 蓝牙串口接口
        input bt_rx,            // 蓝牙串口RX(接收手机数据)
        output bt_tx,           // 蓝牙串口TX(发送到手机)

        // CH340串口接口(用于反向传输 - 连接到UART MUX)
        input uart_rx,          // CH340 RX(预留,用于发送AT指令配置蓝牙)
        output [7:0] uart_tx_data,    // 发送数据(连到MUX)
        output uart_tx_send_en,       // 发送使能(连到MUX)
        input uart_tx_done,           // 发送完成(来自MUX)
        input uart_tx_busy,           // CH340发送忙标志

        // 控制信号
        input bt_enable,        // 蓝牙功能使能
        input [31:0] baud_rate_cfg,  // 蓝牙波特率配置(来自命令解析器)

        // 状态输出
        output reg [7:0] status // 状态寄存器
    );

    parameter CLK_FREQ = 50000000;  // 50MHz系统时钟

    //=========================================================================
    // 蓝牙波特率寄存器
    //=========================================================================
    reg [31:0] baud_rate_reg;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            baud_rate_reg <= 32'd115200;  // 默认115200
        else
            baud_rate_reg <= baud_rate_cfg;  // 从配置接口更新
    end

    //=========================================================================
    // 蓝牙串口发送模块 (CDC -> 蓝牙 -> 手机)
    // 使用可配置波特率
    //=========================================================================
    wire bt_tx_done;
    reg bt_tx_send_en;
    reg [7:0] bt_tx_data;

    uart_byte_tx u_bt_tx(
                     .Clk        (clk),
                     .Rst_n      (rst_n),
                     .data_byte  (bt_tx_data),
                     .send_en    (bt_tx_send_en),
                     .Baud_Rate  (baud_rate_reg),  // 使用可配置的波特率
                     .Clk_Freq   (CLK_FREQ),
                     .uart_tx    (bt_tx),
                     .Tx_Done    (bt_tx_done),
                     .uart_state ()
                 );

    //=========================================================================
    // 蓝牙串口接收模块 (手机 -> 蓝牙 -> FPGA)
    // 使用可配置波特率
    //=========================================================================
    wire [7:0] bt_rx_data;
    wire bt_rx_done;

    uart_byte_rx u_bt_rx(
                     .Clk        (clk),
                     .Rst_n      (rst_n),
                     .Baud_Rate  (baud_rate_reg),  // 使用可配置的波特率
                     .uart_rx    (bt_rx),
                     .data_byte  (bt_rx_data),
                     .Rx_Done    (bt_rx_done)
                 );

    //=========================================================================
    // CH340串口发送接口 (连接到顶层UART MUX,不直接例化uart_byte_tx)
    //=========================================================================
    reg uart_tx_send_en_int;
    reg [7:0] uart_tx_data_int;

    assign uart_tx_data = uart_tx_data_int;
    assign uart_tx_send_en = uart_tx_send_en_int;

    //=========================================================================
    // 数据通道1: CDC -> 蓝牙
    // 将CDC接收到的数据转发给蓝牙模块
    //=========================================================================
    reg cdc_rx_valid_d1;
    wire cdc_rx_posedge;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            cdc_rx_valid_d1 <= 1'b0;
        else
            cdc_rx_valid_d1 <= cdc_rx_valid;
    end

    assign cdc_rx_posedge = cdc_rx_valid && (!cdc_rx_valid_d1);

    // CDC到蓝牙发送状态机
    localparam CDC2BT_IDLE = 2'd0;
    localparam CDC2BT_SEND = 2'd1;
    localparam CDC2BT_WAIT = 2'd2;

    reg [1:0] cdc2bt_state;

    // 发送完成信号：当状态机从WAIT回到IDLE时产生一个周期脉冲
    assign cdc_tx_done = (cdc2bt_state == CDC2BT_WAIT) && bt_tx_done;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cdc2bt_state <= CDC2BT_IDLE;
            bt_tx_send_en <= 1'b0;
            bt_tx_data <= 8'h0;
        end
        else if (bt_enable) begin
            case (cdc2bt_state)
                CDC2BT_IDLE: begin
                    bt_tx_send_en <= 1'b0;
                    if (cdc_rx_posedge) begin
                        bt_tx_data <= cdc_rx_data;
                        cdc2bt_state <= CDC2BT_SEND;
                    end
                end

                CDC2BT_SEND: begin
                    bt_tx_send_en <= 1'b1;
                    cdc2bt_state <= CDC2BT_WAIT;
                end

                CDC2BT_WAIT: begin
                    bt_tx_send_en <= 1'b0;
                    if (bt_tx_done)
                        cdc2bt_state <= CDC2BT_IDLE;
                end

                default:
                    cdc2bt_state <= CDC2BT_IDLE;
            endcase
        end
        else begin
            cdc2bt_state <= CDC2BT_IDLE;
            bt_tx_send_en <= 1'b0;
        end
    end

    //=========================================================================
    // 数据通道2: 蓝牙 -> CH340
    // 将蓝牙接收到的数据转发给CH340(上位机)
    // 添加16字节FIFO缓冲，防止数据丢失
    //=========================================================================

    // 接收FIFO
    reg [7:0] rx_fifo [0:15];
    reg [3:0] rx_wr_ptr;
    reg [3:0] rx_rd_ptr;
    wire rx_fifo_empty;
    wire rx_fifo_full;

    assign rx_fifo_empty = (rx_wr_ptr == rx_rd_ptr);
    assign rx_fifo_full = ((rx_wr_ptr + 4'd1) == rx_rd_ptr);

    reg bt_rx_done_d1;
    wire bt_rx_posedge;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            bt_rx_done_d1 <= 1'b0;
        else
            bt_rx_done_d1 <= bt_rx_done;
    end

    assign bt_rx_posedge = bt_rx_done && (!bt_rx_done_d1);

    // FIFO写入：蓝牙接收到数据时写入FIFO
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rx_wr_ptr <= 4'd0;
        end
        else if (bt_enable && bt_rx_posedge && !rx_fifo_full) begin
            rx_fifo[rx_wr_ptr] <= bt_rx_data;
            rx_wr_ptr <= rx_wr_ptr + 4'd1;
        end
    end

    // 蓝牙到CH340发送状态机（从FIFO读取）
    localparam BT2UART_IDLE = 2'd0;
    localparam BT2UART_SEND = 2'd1;
    localparam BT2UART_WAIT = 2'd2;

    reg [1:0] bt2uart_state;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bt2uart_state <= BT2UART_IDLE;
            uart_tx_send_en_int <= 1'b0;
            uart_tx_data_int <= 8'h0;
            rx_rd_ptr <= 4'd0;
        end
        else if (bt_enable) begin
            case (bt2uart_state)
                BT2UART_IDLE: begin
                    uart_tx_send_en_int <= 1'b0;
                    // 从FIFO读取数据
                    if (!rx_fifo_empty) begin
                        uart_tx_data_int <= rx_fifo[rx_rd_ptr];
                        bt2uart_state <= BT2UART_SEND;
                    end
                end

                BT2UART_SEND: begin
                    uart_tx_send_en_int <= 1'b1;
                    bt2uart_state <= BT2UART_WAIT;
                end

                BT2UART_WAIT: begin
                    uart_tx_send_en_int <= 1'b0;
                    if (uart_tx_done) begin
                        rx_rd_ptr <= rx_rd_ptr + 4'd1;  // 移动读指针
                        bt2uart_state <= BT2UART_IDLE;
                    end
                end

                default:
                    bt2uart_state <= BT2UART_IDLE;
            endcase
        end
        else begin
            bt2uart_state <= BT2UART_IDLE;
            uart_tx_send_en_int <= 1'b0;
        end
    end

    //=========================================================================
    // CDC发送请求(预留,当前不使用USB回传)
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            cdc_tx_req <= 1'b0;
        else
            cdc_tx_req <= 1'b0;  // 暂不使用CDC回传
    end

    //=========================================================================
    // 状态寄存器
    // [0]: 蓝牙使能
    // [1]: CDC->BT忙
    // [2]: BT->UART忙
    // [3]: RX FIFO满（丢数据警告）
    // [7:4]: RX FIFO数据量（字节数）
    //=========================================================================
    wire [3:0] rx_fifo_count;
    assign rx_fifo_count = rx_wr_ptr - rx_rd_ptr;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            status <= 8'h0;
        else begin
            status[0] <= bt_enable;
            status[1] <= (cdc2bt_state != CDC2BT_IDLE);
            status[2] <= (bt2uart_state != BT2UART_IDLE);
            status[3] <= rx_fifo_full;  // FIFO满标志
            status[7:4] <= rx_fifo_count;  // FIFO数据量
        end
    end

endmodule
