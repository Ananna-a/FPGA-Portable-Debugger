//////////////////////////////////////////////////////////////////////////////////
// Module Name: uart_response_tx
// Description: UART应答帧发送控制器(修复版)
//              负责将命令应答打包为标准帧格式并通过UART发送
//              帧格式: AA 55 | ModID | FuncID | Status | Reserved | Checksum
//              注意: 应答帧头为 AA 55，与命令帧头 55 AA 区分
//////////////////////////////////////////////////////////////////////////////////

module uart_response_tx(
        input clk,
        input rst_n,

        // 应答数据接口
        input response_valid,       // 应答有效信号(脉冲)
        input [7:0] mod_id,         // 模块ID
        input [7:0] func_id,        // 功能ID
        input [7:0] status,         // 状态码(0x00=成功)
        input [7:0] data,           // 数据字节（用于I2C读取/扫描结果）
        output reg response_done,   // 应答发送完成

        // UART字节发送接口
        output reg [7:0] tx_data,
        output reg tx_send_en,
        input tx_done               // UART发送完成标志(Tx_Done)
    );

    // 状态机定义
    localparam S_IDLE    = 2'b00;
    localparam S_PREPARE = 2'b01;
    localparam S_SEND    = 2'b10;
    localparam S_WAIT    = 2'b11;

    reg [1:0] state;
    reg [2:0] byte_index;       // 当前发送字节索引(0-6)
    reg [7:0] tx_buffer [0:6];  // 应答帧缓冲区(7字节)

    // 状态机
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= S_IDLE;
            byte_index <= 3'd0;
            tx_send_en <= 1'b0;
            response_done <= 1'b0;
            tx_data <= 8'h0;
        end
        else begin
            case (state)
                S_IDLE: begin
                    response_done <= 1'b0;
                    tx_send_en <= 1'b0;
                    byte_index <= 3'd0;

                    if (response_valid) begin
                        // 构造应答帧（使用不同的帧头以区分命令帧）
                        tx_buffer[0] <= 8'hAA;      // 帧头1（应答帧专用）
                        tx_buffer[1] <= 8'h55;      // 帧头2（应答帧专用）
                        tx_buffer[2] <= mod_id;     // 模块ID
                        tx_buffer[3] <= func_id;    // 功能ID
                        tx_buffer[4] <= status;     // 状态码
                        tx_buffer[5] <= data;       // 数据字节（I2C读取/扫描结果）
                        // 计算校验和: ModID + FuncID + Status + Data
                        tx_buffer[6] <= (mod_id + func_id + status + data) & 8'hFF;

                        state <= S_PREPARE;
                    end
                end

                S_PREPARE: begin
                    // 准备发送第一个字节
                    tx_data <= tx_buffer[0];
                    tx_send_en <= 1'b1;
                    byte_index <= 3'd1;
                    state <= S_WAIT;
                end

                S_WAIT: begin
                    // 等待UART接收发送请求
                    tx_send_en <= 1'b0;

                    if (tx_done) begin
                        // 当前字节发送完成
                        if (byte_index < 3'd7) begin
                            state <= S_SEND;
                        end
                        else begin
                            // 所有字节发送完成
                            response_done <= 1'b1;
                            state <= S_IDLE;
                        end
                    end
                end

                S_SEND: begin
                    // 发送下一个字节
                    tx_data <= tx_buffer[byte_index];
                    tx_send_en <= 1'b1;
                    byte_index <= byte_index + 3'd1;
                    state <= S_WAIT;
                end

                default:
                    state <= S_IDLE;
            endcase
        end
    end

endmodule
