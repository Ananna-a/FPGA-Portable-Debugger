/****************************************************************************
 * SPI主机核心模块 - V1.3 参考I2C实现
 * 
 * 功能：
 *   - 可配置CPOL/CPHA（支持Mode 0/1/2/3）
 *   - 可配置MSB/LSB First
 *   - 可配置SPI频率（1M/2M/4M/8M/12M）
 *   - 字节级传输（收发同步）
 *   - 自动CS控制（超时自动拉高）
 * 
 * 优化内容 (V1.3):
 *   - ✅ 参考I2C实现：配置参数直接使用输入信号，无寄存器锁存
 *   - ✅ 默认1MHz时钟（适配24MHz逻辑分析仪）
 *   - ✅ 空闲时实时计算分频系数（配置立即生效）
 * 
 * 作者: AI Assistant (参考i2c_control.v)
 * 日期: 2025-11-01
 * 版本: V1.3
 ****************************************************************************/

module spi_master_core (
        input wire clk,                    // 系统时钟 50MHz
        input wire rst_n,                  // 复位信号（高有效）

        // 配置接口
        input wire [15:0] spi_freq_khz,    // SPI频率（KHz）
        input wire cpol,                   // 时钟极性（0=空闲低，1=空闲高）
        input wire cpha,                   // 时钟相位（0=第一边沿采样，1=第二边沿采样）
        input wire msb_first,              // 1=MSB先发，0=LSB先发

        // 传输接口
        input wire [7:0] tx_data,          // 发送数据
        input wire trans_en,               // 传输使能（单周期脉冲）
        output reg [7:0] rx_data,          // 接收数据
        output reg trans_done,             // 传输完成
        output reg spi_busy,               // 忙标志

        // SPI物理接口
        output reg spi_cs,                 // 片选（低有效）
        output reg spi_sclk,               // 时钟
        output reg spi_mosi,               // 主出从入
        input wire spi_miso                // 主入从出
    );

    //=========================================================================
    // 参数定义
    //=========================================================================
    localparam TIMEOUT_CNT_MAX = 48;   // 超时计数（CS自动拉高）

    //=========================================================================
    // SPI时钟分频 - 参考I2C实现（直接使用输入信号）
    //=========================================================================
    reg [31:0] clk_div_max;
    reg [31:0] clk_div_cnt;
    reg spi_clk_x2;                    // SPI时钟2倍频脉冲

    // 系统时钟：50MHz
    // SPI时钟需要2倍频（偶数计数）
    // 分频系数 = 50000 / (2 * spi_freq_khz)

    // ✅ 有效频率计算（参考I2C：直接使用输入信号）
    wire [15:0] spi_freq_khz_valid;
    assign spi_freq_khz_valid = (spi_freq_khz == 16'd0) ? 16'd1000 : spi_freq_khz;

    // ✅ 动态分频系数计算（空闲时实时更新）
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            clk_div_max <= 32'd25;  // 默认1MHz
        end
        else if (!spi_busy) begin
            // ✅ 空闲时根据输入信号实时计算（不依赖寄存器锁存）
            case (spi_freq_khz_valid)
                16'd1000:
                    clk_div_max <= 32'd25;  // 1MHz (精确)
                16'd2000:
                    clk_div_max <= 32'd12;  // 2MHz (实际2.08MHz)
                16'd4000:
                    clk_div_max <= 32'd6;   // 4MHz (实际4.17MHz)
                16'd8000:
                    clk_div_max <= 32'd3;   // 8MHz (实际8.33MHz)
                16'd12000:
                    clk_div_max <= 32'd2;   // 12MHz (实际12.5MHz)
                default:
                    clk_div_max <= 32'd25;  // 其他默认1MHz
            endcase
        end
    end

    // 分频计数器
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            clk_div_cnt <= 32'd0;
            spi_clk_x2 <= 1'b0;
        end
        else if (!spi_busy) begin
            // 空闲时重置计数器
            clk_div_cnt <= 32'd0;
            spi_clk_x2 <= 1'b0;
        end
        else if (clk_div_cnt >= clk_div_max - 1'd1) begin
            clk_div_cnt <= 32'd0;
            spi_clk_x2 <= 1'b1;      // 产生脉冲
        end
        else begin
            clk_div_cnt <= clk_div_cnt + 1'd1;
            spi_clk_x2 <= 1'b0;
        end
    end

    //=========================================================================
    // 数据寄存器
    //=========================================================================
    reg [7:0] rx_data_r;               // 接收移位寄存器
    reg [7:0] tx_data_r;               // 发送移位寄存器
    reg [4:0] spi_state_cnt;           // 状态计数器（0-17）

    //=========================================================================
    // 传输控制逻辑
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            spi_busy <= 1'b0;
            rx_data <= 8'h00;
            tx_data_r <= 8'h00;
        end
        else if (trans_en) begin
            // 启动传输
            spi_busy <= 1'b1;

            // ✅ 修复：参考上一版本 - MSB_FIRST=1时不反转
            // MSB_FIRST = 1: 保持原序（bit7先发）
            // MSB_FIRST = 0: 反转顺序（bit0先发）
            if (msb_first)
                tx_data_r <= tx_data;  // 不反转
            else
                tx_data_r <= {tx_data[0], tx_data[1], tx_data[2], tx_data[3],
                              tx_data[4], tx_data[5], tx_data[6], tx_data[7]};  // 反转

            rx_data <= rx_data;
        end
        else if (spi_state_cnt >= (5'd17 - {4'b0, cpha})) begin
            // 传输完成
            spi_busy <= 1'b0;

            // ✅ 修复：参考上一版本
            // MSB_FIRST = 1: 保持原序
            // MSB_FIRST = 0: 反转顺序
            if (msb_first)
                rx_data <= rx_data_r;  // 不反转
            else
                rx_data <= {rx_data_r[0], rx_data_r[1], rx_data_r[2], rx_data_r[3],
                            rx_data_r[4], rx_data_r[5], rx_data_r[6], rx_data_r[7]};  // 反转
        end
        else begin
            spi_busy <= spi_busy;
            rx_data <= rx_data;
        end
    end

    //=========================================================================
    // SPI时钟生成
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            spi_state_cnt <= 5'd0;
            spi_sclk <= 1'b0;  // 复位时默认低电平
        end
        else if (spi_state_cnt >= (5'd17 - {4'b0, cpha})) begin
            // 传输完成，恢复空闲状态
            spi_sclk <= cpol;
            spi_state_cnt <= 5'd0;
        end
        else if (!spi_busy) begin
            // ✅ 空闲时立即应用cpol配置（直接使用输入信号）
            spi_sclk <= cpol;
            spi_state_cnt <= 5'd0;
        end
        else if (spi_clk_x2) begin
            if (spi_busy) begin
                // CPHA=0且第一个状态时，不翻转时钟
                if ((cpha == 1'b0) && (spi_state_cnt == 5'd0))
                    spi_sclk <= spi_sclk;
                else
                    spi_sclk <= ~spi_sclk;

                spi_state_cnt <= spi_state_cnt + 1'd1;
            end
            else begin
                spi_sclk <= cpol;
                spi_state_cnt <= 5'd0;
            end
        end
        else begin
            spi_state_cnt <= spi_state_cnt;
        end
    end

    //=========================================================================
    // MOSI输出和MISO采样
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            spi_mosi <= 1'b0;
            rx_data_r <= 8'h00;
        end
        else if (spi_clk_x2) begin
            if (spi_state_cnt >= 5'd16) begin
                // 传输结束
                spi_mosi <= 1'b0;
                rx_data_r <= rx_data_r;
            end
            else if (~spi_state_cnt[0]) begin
                // ✅ 修复：偶数状态设置MOSI - 参考上一版本
                // 发送顺序：bit7→bit6→...→bit0 (MSB first)
                spi_mosi <= tx_data_r[7 - spi_state_cnt[4:1]];
            end
            else begin
                // 奇数状态：采样MISO
                rx_data_r[7 - spi_state_cnt[4:1]] <= spi_miso;
            end
        end
        else begin
            spi_mosi <= spi_mosi;
            rx_data_r <= rx_data_r;
        end
    end

    //=========================================================================
    // 传输完成信号
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            trans_done <= 1'b0;
        end
        else if (spi_state_cnt >= (5'd17 - {4'b0, cpha}))
            trans_done <= 1'b1;
        else
            trans_done <= 1'b0;
    end

    //=========================================================================
    // CS控制（自动超时拉高）- 修复时序问题
    //=========================================================================
    reg [15:0] timeout_cnt;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            spi_cs <= 1'b1;              // CS空闲高
            timeout_cnt <= 16'd0;
        end
        else begin
            if (trans_en) begin
                // ✅ 开始传输，立即拉低CS
                spi_cs <= 1'b0;
                timeout_cnt <= 16'd0;
            end
            else if (spi_busy) begin
                // ✅ 传输中保持CS低电平
                spi_cs <= 1'b0;
                timeout_cnt <= 16'd0;
            end
            else if (!spi_busy && spi_cs == 1'b0) begin
                // ✅ 传输完成后开始超时计数
                if (timeout_cnt >= TIMEOUT_CNT_MAX - 1'd1) begin
                    // 超时，拉高CS
                    spi_cs <= 1'b1;
                    timeout_cnt <= 16'd0;
                end
                else begin
                    timeout_cnt <= timeout_cnt + 1'd1;
                    spi_cs <= 1'b0;      // 保持低电平
                end
            end
            else begin
                // CS已经为高，保持
                spi_cs <= 1'b1;
                timeout_cnt <= 16'd0;
            end
        end
    end

endmodule
