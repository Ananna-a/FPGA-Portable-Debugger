//=============================================================================
// 变长命令帧解析模块
// 协议格式: 55 AA [CMD] [LEN_L] [LEN_H] [PAYLOAD...] [CS]
// 说明: 支持0-512字节的变长Payload
//=============================================================================

module cdc_cmd_parser(
        input Clk,
        input Reset_n,

        // 接收数据接口
        input [7:0] rx_data,        // 接收到的字节
        input rx_valid,             // 数据有效标志

        // 解析输出
        output reg [7:0] cmd,       // 命令码
        output reg [15:0] length,   // Payload长度
        output reg [7:0] payload_data,  // Payload数据（逐字节输出）
        output reg payload_valid,   // Payload数据有效
        output reg cmd_done,        // 整帧解析完成
        output reg cmd_error,       // 帧错误（校验失败等）
        output reg cmd_valid_pulse  // 命令码有效脉冲（在S_CMD状态产生）
    );

    //=========================================================================
    // 状态机定义
    //=========================================================================
    localparam S_IDLE       = 4'd0;   // 空闲，等待帧头1
    localparam S_HEAD2      = 4'd1;   // 等待帧头2
    localparam S_CMD        = 4'd2;   // 接收命令码
    localparam S_LEN_L      = 4'd3;   // 接收长度低字节
    localparam S_LEN_H      = 4'd4;   // 接收长度高字节
    localparam S_PAYLOAD    = 4'd5;   // 接收Payload
    localparam S_CS         = 4'd6;   // 接收校验和
    localparam S_DONE       = 4'd7;   // 完成
    localparam S_ERROR      = 4'd8;   // 错误

    reg [3:0] state;
    reg [15:0] payload_cnt;           // Payload计数器
    reg [7:0] checksum_calc;          // 计算的校验和
    reg [7:0] checksum_rx;            // 接收的校验和

    //=========================================================================
    // 状态机
    //=========================================================================
    always @(posedge Clk or negedge Reset_n) begin
        if (!Reset_n) begin
            state <= S_IDLE;
            cmd <= 8'h0;
            length <= 16'h0;
            payload_data <= 8'h0;
            payload_valid <= 1'b0;
            cmd_done <= 1'b0;
            cmd_error <= 1'b0;
            cmd_valid_pulse <= 1'b0;
            payload_cnt <= 16'h0;
            checksum_calc <= 8'h0;
            checksum_rx <= 8'h0;
        end
        else begin
            // 默认输出
            payload_valid <= 1'b0;
            cmd_done <= 1'b0;
            cmd_error <= 1'b0;
            cmd_valid_pulse <= 1'b0;  // 默认清零

            case (state)
                //-------------------------------------------------------------
                // 空闲状态，等待帧头1 (0x55)
                //-------------------------------------------------------------
                S_IDLE: begin
                    if (rx_valid && rx_data == 8'h55) begin
                        state <= S_HEAD2;
                        checksum_calc <= 8'h0;
                    end
                end

                //-------------------------------------------------------------
                // 等待帧头2 (0xAA)
                //-------------------------------------------------------------
                S_HEAD2: begin
                    if (rx_valid) begin
                        if (rx_data == 8'hAA) begin
                            state <= S_CMD;
                        end
                        else begin
                            // 帧头错误，重新搜索
                            state <= (rx_data == 8'h55) ? S_HEAD2 : S_IDLE;
                        end
                    end
                end

                //-------------------------------------------------------------
                // 接收命令码
                //-------------------------------------------------------------
                S_CMD: begin
                    if (rx_valid) begin
                        cmd <= rx_data;
                        cmd_valid_pulse <= 1'b1;  // 产生命令有效脉冲
                        checksum_calc <= rx_data;  // 开始累加校验和
                        state <= S_LEN_L;
                    end
                end

                //-------------------------------------------------------------
                // 接收长度低字节
                //-------------------------------------------------------------
                S_LEN_L: begin
                    if (rx_valid) begin
                        length[7:0] <= rx_data;
                        checksum_calc <= checksum_calc + rx_data;
                        state <= S_LEN_H;
                    end
                end

                //-------------------------------------------------------------
                // 接收长度高字节
                //-------------------------------------------------------------
                S_LEN_H: begin
                    if (rx_valid) begin
                        length[15:8] <= rx_data;
                        checksum_calc <= checksum_calc + rx_data;
                        payload_cnt <= 16'h0;

                        // 如果长度为0，直接跳到校验和
                        if ({rx_data, length[7:0]} == 16'h0)
                            state <= S_CS;
                        else
                            state <= S_PAYLOAD;
                    end
                end

                //-------------------------------------------------------------
                // 接收Payload
                //-------------------------------------------------------------
                S_PAYLOAD: begin
                    if (rx_valid) begin
                        payload_data <= rx_data;
                        payload_valid <= 1'b1;
                        checksum_calc <= checksum_calc + rx_data;
                        payload_cnt <= payload_cnt + 1;

                        // Payload接收完成
                        if (payload_cnt + 1 >= length)
                            state <= S_CS;
                    end
                end

                //-------------------------------------------------------------
                // 接收校验和
                //-------------------------------------------------------------
                S_CS: begin
                    if (rx_valid) begin
                        checksum_rx <= rx_data;

                        // 校验
                        if (rx_data == checksum_calc) begin
                            state <= S_DONE;
                        end
                        else begin
                            state <= S_ERROR;
                        end
                    end
                end

                //-------------------------------------------------------------
                // 解析完成
                //-------------------------------------------------------------
                S_DONE: begin
                    cmd_done <= 1'b1;
                    state <= S_IDLE;
                end

                //-------------------------------------------------------------
                // 错误状态
                //-------------------------------------------------------------
                S_ERROR: begin
                    cmd_error <= 1'b1;
                    state <= S_IDLE;
                end

                default:
                    state <= S_IDLE;
            endcase
        end
    end

endmodule
