//=============================================================================
// 串行序列播放引擎 V3.0（32位DDS，高精度）
// 功能：
//   - 8个通道独立播放比特序列
//   - 每个通道使用32位DDS累加器（支持0.01Hz-25MHz，高精度）
//   - 每个通道有独立的序列长度
//   - 支持通道使能掩码
// 改进：
//   - 采用DDS相位累加器替代分频计数器
//   - 频率精度：约0.01Hz（32位@50MHz）
//   - 参考PWM模块的DDS实现
//   - 三级流水线设计，消除高频时序问题（5MHz+）
//     * 第一级：锁存地址
//     * 第二级：读取RAM数据
//     * 第三级：应用enable并输出
//=============================================================================

module sequence_playback_serial_v3(
        input clk,                  // 系统时钟（50MHz）
        input rst_n,
        input enable,               // 播放使能

        // 配置参数
        input [7:0] channel_mask,   // 通道使能掩码
        input [7:0] seq_len_ch0,    // 通道0序列长度
        input [7:0] seq_len_ch1,
        input [7:0] seq_len_ch2,
        input [7:0] seq_len_ch3,
        input [7:0] seq_len_ch4,
        input [7:0] seq_len_ch5,
        input [7:0] seq_len_ch6,
        input [7:0] seq_len_ch7,

        // 每通道独立32位DDS频率字
        input [31:0] freq_word_ch0,
        input [31:0] freq_word_ch1,
        input [31:0] freq_word_ch2,
        input [31:0] freq_word_ch3,
        input [31:0] freq_word_ch4,
        input [31:0] freq_word_ch5,
        input [31:0] freq_word_ch6,
        input [31:0] freq_word_ch7,

        // 每个通道的RAM（展平为256位）
        input [255:0] serial_ram_0,
        input [255:0] serial_ram_1,
        input [255:0] serial_ram_2,
        input [255:0] serial_ram_3,
        input [255:0] serial_ram_4,
        input [255:0] serial_ram_5,
        input [255:0] serial_ram_6,
        input [255:0] serial_ram_7,

        // 序列输出
        output [7:0] seq_out        // 8通道输出
    );

    //=========================================================================
    // 每个通道独立DDS相位累加器（32位）
    //=========================================================================
    reg [31:0] phase_acc_ch0, phase_acc_ch1, phase_acc_ch2, phase_acc_ch3;
    reg [31:0] phase_acc_ch4, phase_acc_ch5, phase_acc_ch6, phase_acc_ch7;

    // 上一周期的相位MSB（用于边沿检测）
    reg phase_msb_prev_ch0, phase_msb_prev_ch1, phase_msb_prev_ch2, phase_msb_prev_ch3;
    reg phase_msb_prev_ch4, phase_msb_prev_ch5, phase_msb_prev_ch6, phase_msb_prev_ch7;

    // Tick信号：相位MSB从1→0跳变时产生tick
    reg tick_ch0, tick_ch1, tick_ch2, tick_ch3;
    reg tick_ch4, tick_ch5, tick_ch6, tick_ch7;

    // 通道0 DDS累加器
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase_acc_ch0 <= 32'd0;
            phase_msb_prev_ch0 <= 1'b0;
            tick_ch0 <= 1'b0;
        end
        else if (enable && channel_mask[0]) begin
            phase_acc_ch0 <= phase_acc_ch0 + freq_word_ch0;
            phase_msb_prev_ch0 <= phase_acc_ch0[31];
            // 检测MSB从1→0的跳变
            tick_ch0 <= phase_msb_prev_ch0 & ~phase_acc_ch0[31];
        end
        else begin
            phase_acc_ch0 <= 32'd0;
            phase_msb_prev_ch0 <= 1'b0;
            tick_ch0 <= 1'b0;
        end
    end

    // 通道1 DDS累加器
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase_acc_ch1 <= 32'd0;
            phase_msb_prev_ch1 <= 1'b0;
            tick_ch1 <= 1'b0;
        end
        else if (enable && channel_mask[1]) begin
            phase_acc_ch1 <= phase_acc_ch1 + freq_word_ch1;
            phase_msb_prev_ch1 <= phase_acc_ch1[31];
            tick_ch1 <= phase_msb_prev_ch1 & ~phase_acc_ch1[31];
        end
        else begin
            phase_acc_ch1 <= 32'd0;
            phase_msb_prev_ch1 <= 1'b0;
            tick_ch1 <= 1'b0;
        end
    end

    // 通道2 DDS累加器
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase_acc_ch2 <= 32'd0;
            phase_msb_prev_ch2 <= 1'b0;
            tick_ch2 <= 1'b0;
        end
        else if (enable && channel_mask[2]) begin
            phase_acc_ch2 <= phase_acc_ch2 + freq_word_ch2;
            phase_msb_prev_ch2 <= phase_acc_ch2[31];
            tick_ch2 <= phase_msb_prev_ch2 & ~phase_acc_ch2[31];
        end
        else begin
            phase_acc_ch2 <= 32'd0;
            phase_msb_prev_ch2 <= 1'b0;
            tick_ch2 <= 1'b0;
        end
    end

    // 通道3 DDS累加器
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase_acc_ch3 <= 32'd0;
            phase_msb_prev_ch3 <= 1'b0;
            tick_ch3 <= 1'b0;
        end
        else if (enable && channel_mask[3]) begin
            phase_acc_ch3 <= phase_acc_ch3 + freq_word_ch3;
            phase_msb_prev_ch3 <= phase_acc_ch3[31];
            tick_ch3 <= phase_msb_prev_ch3 & ~phase_acc_ch3[31];
        end
        else begin
            phase_acc_ch3 <= 32'd0;
            phase_msb_prev_ch3 <= 1'b0;
            tick_ch3 <= 1'b0;
        end
    end

    // 通道4 DDS累加器
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase_acc_ch4 <= 32'd0;
            phase_msb_prev_ch4 <= 1'b0;
            tick_ch4 <= 1'b0;
        end
        else if (enable && channel_mask[4]) begin
            phase_acc_ch4 <= phase_acc_ch4 + freq_word_ch4;
            phase_msb_prev_ch4 <= phase_acc_ch4[31];
            tick_ch4 <= phase_msb_prev_ch4 & ~phase_acc_ch4[31];
        end
        else begin
            phase_acc_ch4 <= 32'd0;
            phase_msb_prev_ch4 <= 1'b0;
            tick_ch4 <= 1'b0;
        end
    end

    // 通道5 DDS累加器
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase_acc_ch5 <= 32'd0;
            phase_msb_prev_ch5 <= 1'b0;
            tick_ch5 <= 1'b0;
        end
        else if (enable && channel_mask[5]) begin
            phase_acc_ch5 <= phase_acc_ch5 + freq_word_ch5;
            phase_msb_prev_ch5 <= phase_acc_ch5[31];
            tick_ch5 <= phase_msb_prev_ch5 & ~phase_acc_ch5[31];
        end
        else begin
            phase_acc_ch5 <= 32'd0;
            phase_msb_prev_ch5 <= 1'b0;
            tick_ch5 <= 1'b0;
        end
    end

    // 通道6 DDS累加器
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase_acc_ch6 <= 32'd0;
            phase_msb_prev_ch6 <= 1'b0;
            tick_ch6 <= 1'b0;
        end
        else if (enable && channel_mask[6]) begin
            phase_acc_ch6 <= phase_acc_ch6 + freq_word_ch6;
            phase_msb_prev_ch6 <= phase_acc_ch6[31];
            tick_ch6 <= phase_msb_prev_ch6 & ~phase_acc_ch6[31];
        end
        else begin
            phase_acc_ch6 <= 32'd0;
            phase_msb_prev_ch6 <= 1'b0;
            tick_ch6 <= 1'b0;
        end
    end

    // 通道7 DDS累加器
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase_acc_ch7 <= 32'd0;
            phase_msb_prev_ch7 <= 1'b0;
            tick_ch7 <= 1'b0;
        end
        else if (enable && channel_mask[7]) begin
            phase_acc_ch7 <= phase_acc_ch7 + freq_word_ch7;
            phase_msb_prev_ch7 <= phase_acc_ch7[31];
            tick_ch7 <= phase_msb_prev_ch7 & ~phase_acc_ch7[31];
        end
        else begin
            phase_acc_ch7 <= 32'd0;
            phase_msb_prev_ch7 <= 1'b0;
            tick_ch7 <= 1'b0;
        end
    end

    //=========================================================================
    // 每个通道独立地址生成器（位地址）
    //=========================================================================
    reg [7:0] bit_addr_ch0, bit_addr_ch1, bit_addr_ch2, bit_addr_ch3;
    reg [7:0] bit_addr_ch4, bit_addr_ch5, bit_addr_ch6, bit_addr_ch7;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bit_addr_ch0 <= 8'h0;
            bit_addr_ch1 <= 8'h0;
            bit_addr_ch2 <= 8'h0;
            bit_addr_ch3 <= 8'h0;
            bit_addr_ch4 <= 8'h0;
            bit_addr_ch5 <= 8'h0;
            bit_addr_ch6 <= 8'h0;
            bit_addr_ch7 <= 8'h0;
        end
        else if (!enable) begin
            // 全局失能时清零所有地址
            bit_addr_ch0 <= 8'h0;
            bit_addr_ch1 <= 8'h0;
            bit_addr_ch2 <= 8'h0;
            bit_addr_ch3 <= 8'h0;
            bit_addr_ch4 <= 8'h0;
            bit_addr_ch5 <= 8'h0;
            bit_addr_ch6 <= 8'h0;
            bit_addr_ch7 <= 8'h0;
        end
        else begin
            // 通道0地址生成
            if (!channel_mask[0]) begin
                bit_addr_ch0 <= 8'h0;
            end
            else if (tick_ch0) begin
                if (bit_addr_ch0 >= (seq_len_ch0 - 8'd1))
                    bit_addr_ch0 <= 8'h0;
                else
                    bit_addr_ch0 <= bit_addr_ch0 + 8'd1;
            end

            // 通道1地址生成
            if (!channel_mask[1]) begin
                bit_addr_ch1 <= 8'h0;
            end
            else if (tick_ch1) begin
                if (bit_addr_ch1 >= (seq_len_ch1 - 8'd1))
                    bit_addr_ch1 <= 8'h0;
                else
                    bit_addr_ch1 <= bit_addr_ch1 + 8'd1;
            end

            // 通道2地址生成
            if (!channel_mask[2]) begin
                bit_addr_ch2 <= 8'h0;
            end
            else if (tick_ch2) begin
                if (bit_addr_ch2 >= (seq_len_ch2 - 8'd1))
                    bit_addr_ch2 <= 8'h0;
                else
                    bit_addr_ch2 <= bit_addr_ch2 + 8'd1;
            end

            // 通道3地址生成
            if (!channel_mask[3]) begin
                bit_addr_ch3 <= 8'h0;
            end
            else if (tick_ch3) begin
                if (bit_addr_ch3 >= (seq_len_ch3 - 8'd1))
                    bit_addr_ch3 <= 8'h0;
                else
                    bit_addr_ch3 <= bit_addr_ch3 + 8'd1;
            end

            // 通道4地址生成
            if (!channel_mask[4]) begin
                bit_addr_ch4 <= 8'h0;
            end
            else if (tick_ch4) begin
                if (bit_addr_ch4 >= (seq_len_ch4 - 8'd1))
                    bit_addr_ch4 <= 8'h0;
                else
                    bit_addr_ch4 <= bit_addr_ch4 + 8'd1;
            end

            // 通道5地址生成
            if (!channel_mask[5]) begin
                bit_addr_ch5 <= 8'h0;
            end
            else if (tick_ch5) begin
                if (bit_addr_ch5 >= (seq_len_ch5 - 8'd1))
                    bit_addr_ch5 <= 8'h0;
                else
                    bit_addr_ch5 <= bit_addr_ch5 + 8'd1;
            end

            // 通道6地址生成
            if (!channel_mask[6]) begin
                bit_addr_ch6 <= 8'h0;
            end
            else if (tick_ch6) begin
                if (bit_addr_ch6 >= (seq_len_ch6 - 8'd1))
                    bit_addr_ch6 <= 8'h0;
                else
                    bit_addr_ch6 <= bit_addr_ch6 + 8'd1;
            end

            // 通道7地址生成
            if (!channel_mask[7]) begin
                bit_addr_ch7 <= 8'h0;
            end
            else if (tick_ch7) begin
                if (bit_addr_ch7 >= (seq_len_ch7 - 8'd1))
                    bit_addr_ch7 <= 8'h0;
                else
                    bit_addr_ch7 <= bit_addr_ch7 + 8'd1;
            end
        end
    end

    //=========================================================================
    // 每个通道独立读取并输出（三级流水线，消除高频时序问题）
    // 参考PWM模块的三级流水线设计
    //=========================================================================

    // 第一级：锁存地址（避免地址变化时的亚稳态）
    reg [7:0] bit_addr_ch0_r, bit_addr_ch1_r, bit_addr_ch2_r, bit_addr_ch3_r;
    reg [7:0] bit_addr_ch4_r, bit_addr_ch5_r, bit_addr_ch6_r, bit_addr_ch7_r;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bit_addr_ch0_r <= 8'h0;
            bit_addr_ch1_r <= 8'h0;
            bit_addr_ch2_r <= 8'h0;
            bit_addr_ch3_r <= 8'h0;
            bit_addr_ch4_r <= 8'h0;
            bit_addr_ch5_r <= 8'h0;
            bit_addr_ch6_r <= 8'h0;
            bit_addr_ch7_r <= 8'h0;
        end
        else begin
            bit_addr_ch0_r <= bit_addr_ch0;
            bit_addr_ch1_r <= bit_addr_ch1;
            bit_addr_ch2_r <= bit_addr_ch2;
            bit_addr_ch3_r <= bit_addr_ch3;
            bit_addr_ch4_r <= bit_addr_ch4;
            bit_addr_ch5_r <= bit_addr_ch5;
            bit_addr_ch6_r <= bit_addr_ch6;
            bit_addr_ch7_r <= bit_addr_ch7;
        end
    end

    // 第二级：根据锁存的地址读取RAM数据
    reg [7:0] seq_data_reg;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            seq_data_reg <= 8'h0;
        end
        else begin
            seq_data_reg[0] <= channel_mask[0] ? serial_ram_0[bit_addr_ch0_r] : 1'b0;
            seq_data_reg[1] <= channel_mask[1] ? serial_ram_1[bit_addr_ch1_r] : 1'b0;
            seq_data_reg[2] <= channel_mask[2] ? serial_ram_2[bit_addr_ch2_r] : 1'b0;
            seq_data_reg[3] <= channel_mask[3] ? serial_ram_3[bit_addr_ch3_r] : 1'b0;
            seq_data_reg[4] <= channel_mask[4] ? serial_ram_4[bit_addr_ch4_r] : 1'b0;
            seq_data_reg[5] <= channel_mask[5] ? serial_ram_5[bit_addr_ch5_r] : 1'b0;
            seq_data_reg[6] <= channel_mask[6] ? serial_ram_6[bit_addr_ch6_r] : 1'b0;
            seq_data_reg[7] <= channel_mask[7] ? serial_ram_7[bit_addr_ch7_r] : 1'b0;
        end
    end

    // 第三级：应用全局enable并输出
    reg [7:0] seq_out_reg;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            seq_out_reg <= 8'h0;
        end
        else if (enable) begin
            seq_out_reg <= seq_data_reg;
        end
        else begin
            seq_out_reg <= 8'h0;
        end
    end

    assign seq_out = seq_out_reg;

endmodule
