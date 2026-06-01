//=============================================================================
// UART发送多路复用模块（V3.0 - 增加Bode分析仪数据通道）
// 功能: 在频率、CAN_RX、Bode、DSA、SPI、DS18B20、应答帧和蓝牙透传数据之间切换
// 优先级: 频率 > CAN_RX > Bode > DSA > SPI > DS18B20 > 应答帧 > 蓝牙透传
// 新增: Bode分析仪数据通道，优先级高于DSA，确保扫频数据及时发送
// 日期: 2026-01-13
//=============================================================================

module uart_tx_mux(
        input clk,
        input rst_n,

        // CAN接收数据通道 (第2优先级 - CAN接收帧上报)
        input [7:0] can_rx_data,
        input can_rx_send_en,
        output reg can_rx_tx_done,

        // Bode分析仪数据通道 (第3优先级 - Bode扫频数据)
        input [7:0] bode_data,
        input bode_send_en,
        output reg bode_tx_done,

        // DSA数据通道 (第4优先级 - 数字信号测量数据)
        input [7:0] dsa_data,
        input dsa_send_en,
        output reg dsa_tx_done,

        // SPI数据通道 (第4优先级 - Flash读取数据)
        input [7:0] spi_data,
        input spi_send_en,
        output reg spi_tx_done,

        // DS18B20数据通道 (第5优先级 - 温度数据)
        input [7:0] ds18b20_data,
        input ds18b20_send_en,
        output reg ds18b20_tx_done,

        // 频率数据通道 (第6优先级 - 频率测量数据)
        input [7:0] freq_data,
        input freq_send_en,
        output reg freq_tx_done,

        // 应答帧通道 (第7优先级)
        input [7:0] resp_data,
        input resp_send_en,
        output reg resp_tx_done,

        // 蓝牙透传通道 (低优先级)
        input [7:0] bt_data,
        input bt_send_en,
        output reg bt_tx_done,

        // UART底层发送接口
        output reg [7:0] uart_tx_data,
        output reg uart_tx_send_en,
        input uart_tx_done,

        // 调试信号
        output reg [2:0] current_channel  // 0:空闲, 1:CAN_RX, 2:Bode, 3:DSA, 4:SPI, 5:DS18B20, 6:频率, 7:应答/蓝牙
    );

    localparam CH_IDLE = 3'd0;
    localparam CH_CAN_RX = 3'd1;
    localparam CH_BODE = 3'd2;
    localparam CH_DSA  = 3'd3;
    localparam CH_SPI  = 3'd4;
    localparam CH_DS18B20 = 3'd5;
    localparam CH_FREQ = 3'd6;
    localparam CH_RESP = 3'd7;  // 应答帧和蓝牙共用（轮询）

    reg [2:0] state;
    reg can_rx_send_en_d1;
    reg bode_send_en_d1;
    reg dsa_send_en_d1;
    reg spi_send_en_d1;
    reg ds18b20_send_en_d1;
    reg freq_send_en_d1;
    reg resp_send_en_d1;
    reg bt_send_en_d1;

    // 边沿检测
    wire can_rx_send_posedge;
    wire bode_send_posedge;
    wire dsa_send_posedge;
    wire spi_send_posedge;
    wire ds18b20_send_posedge;
    wire freq_send_posedge;
    wire resp_send_posedge;
    wire bt_send_posedge;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            can_rx_send_en_d1 <= 1'b0;
            bode_send_en_d1 <= 1'b0;
            dsa_send_en_d1 <= 1'b0;
            spi_send_en_d1 <= 1'b0;
            ds18b20_send_en_d1 <= 1'b0;
            freq_send_en_d1 <= 1'b0;
            resp_send_en_d1 <= 1'b0;
            bt_send_en_d1 <= 1'b0;
        end
        else begin
            can_rx_send_en_d1 <= can_rx_send_en;
            bode_send_en_d1 <= bode_send_en;
            dsa_send_en_d1 <= dsa_send_en;
            spi_send_en_d1 <= spi_send_en;
            ds18b20_send_en_d1 <= ds18b20_send_en;
            freq_send_en_d1 <= freq_send_en;
            resp_send_en_d1 <= resp_send_en;
            bt_send_en_d1 <= bt_send_en;
        end
    end

    assign can_rx_send_posedge = can_rx_send_en && (!can_rx_send_en_d1);
    assign bode_send_posedge = bode_send_en && (!bode_send_en_d1);
    assign dsa_send_posedge = dsa_send_en && (!dsa_send_en_d1);
    assign spi_send_posedge = spi_send_en && (!spi_send_en_d1);
    assign ds18b20_send_posedge = ds18b20_send_en && (!ds18b20_send_en_d1);
    assign freq_send_posedge = freq_send_en && (!freq_send_en_d1);
    assign resp_send_posedge = resp_send_en && (!resp_send_en_d1);
    assign bt_send_posedge = bt_send_en && (!bt_send_en_d1);

    // 多路复用状态机 - V3.0优先级调整: 频率 > CAN_RX > Bode > DSA > SPI > DS18B20 > 应答帧 > 蓝牙透传
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= CH_IDLE;
            uart_tx_data <= 8'h0;
            uart_tx_send_en <= 1'b0;
            can_rx_tx_done <= 1'b0;
            bode_tx_done <= 1'b0;
            dsa_tx_done <= 1'b0;
            spi_tx_done <= 1'b0;
            ds18b20_tx_done <= 1'b0;
            freq_tx_done <= 1'b0;
            resp_tx_done <= 1'b0;
            bt_tx_done <= 1'b0;
            current_channel <= CH_IDLE;
        end
        else begin
            // 默认清除完成标志
            can_rx_tx_done <= 1'b0;
            bode_tx_done <= 1'b0;
            dsa_tx_done <= 1'b0;
            spi_tx_done <= 1'b0;
            ds18b20_tx_done <= 1'b0;
            freq_tx_done <= 1'b0;
            resp_tx_done <= 1'b0;
            bt_tx_done <= 1'b0;

            case (state)
                CH_IDLE: begin
                    uart_tx_send_en <= 1'b0;
                    current_channel <= CH_IDLE;

                    // 🔥 V9.2.17临时调试：Bode提升到最高优先级（验证后恢复）
                    if (bode_send_posedge) begin
                        uart_tx_data <= bode_data;
                        uart_tx_send_en <= 1'b1;
                        state <= CH_BODE;
                        current_channel <= CH_BODE;
                    end
                    // 优先级2: 频率数据通道（临时降级）
                    else if (freq_send_posedge) begin
                        uart_tx_data <= freq_data;
                        uart_tx_send_en <= 1'b1;
                        state <= CH_FREQ;
                        current_channel <= CH_FREQ;
                    end
                    // 优先级3: CAN接收数据通道
                    else if (can_rx_send_posedge) begin
                        uart_tx_data <= can_rx_data;
                        uart_tx_send_en <= 1'b1;
                        state <= CH_CAN_RX;
                        current_channel <= CH_CAN_RX;
                    end
                    // 优先级4: DSA数据通道
                    else if (dsa_send_posedge) begin
                        uart_tx_data <= dsa_data;
                        uart_tx_send_en <= 1'b1;
                        state <= CH_DSA;
                        current_channel <= CH_DSA;
                    end
                    // 优先级4: SPI数据通道
                    else if (spi_send_posedge) begin
                        uart_tx_data <= spi_data;
                        uart_tx_send_en <= 1'b1;
                        state <= CH_SPI;
                        current_channel <= CH_SPI;
                    end
                    // 优先级5: DS18B20数据通道
                    else if (ds18b20_send_posedge) begin
                        uart_tx_data <= ds18b20_data;
                        uart_tx_send_en <= 1'b1;
                        state <= CH_DS18B20;
                        current_channel <= CH_DS18B20;
                    end
                    // 优先级6: 应答帧
                    else if (resp_send_posedge) begin
                        uart_tx_data <= resp_data;
                        uart_tx_send_en <= 1'b1;
                        state <= CH_RESP;
                        current_channel <= CH_RESP;
                    end
                    // 优先级7: 蓝牙透传（与CH_RESP共用）
                    else if (bt_send_posedge) begin
                        uart_tx_data <= bt_data;
                        uart_tx_send_en <= 1'b1;
                        state <= CH_RESP;
                        current_channel <= CH_RESP;
                    end
                end

                CH_CAN_RX: begin
                    uart_tx_send_en <= 1'b0;
                    if (uart_tx_done) begin
                        can_rx_tx_done <= 1'b1;
                        state <= CH_IDLE;
                    end
                end

                CH_BODE: begin
                    uart_tx_send_en <= 1'b0;
                    if (uart_tx_done) begin
                        bode_tx_done <= 1'b1;
                        state <= CH_IDLE;
                    end
                end

                CH_DSA: begin
                    uart_tx_send_en <= 1'b0;
                    if (uart_tx_done) begin
                        dsa_tx_done <= 1'b1;
                        state <= CH_IDLE;
                    end
                end

                CH_SPI: begin
                    uart_tx_send_en <= 1'b0;
                    if (uart_tx_done) begin
                        spi_tx_done <= 1'b1;
                        state <= CH_IDLE;
                    end
                end

                CH_DS18B20: begin
                    uart_tx_send_en <= 1'b0;
                    if (uart_tx_done) begin
                        ds18b20_tx_done <= 1'b1;
                        state <= CH_IDLE;
                    end
                end

                CH_FREQ: begin
                    uart_tx_send_en <= 1'b0;
                    if (uart_tx_done) begin
                        freq_tx_done <= 1'b1;
                        state <= CH_IDLE;
                    end
                end

                CH_RESP: begin
                    uart_tx_send_en <= 1'b0;
                    if (uart_tx_done) begin
                        resp_tx_done <= 1'b1;
                        bt_tx_done <= 1'b1;  // 蓝牙和应答共用同一通道
                        state <= CH_IDLE;
                    end
                end

                default:
                    state <= CH_IDLE;
            endcase
        end
    end

endmodule
