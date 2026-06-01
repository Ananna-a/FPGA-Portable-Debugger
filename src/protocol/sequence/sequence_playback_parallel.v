//=============================================================================
// 并行序列播放引擎
// 功能：
//   - 从RAM读取字节序列，按照指定频率输出到8通道
//   - 支持循环播放
//   - 使用DDS频率字控制播放速率
//=============================================================================

module sequence_playback_parallel(
        input clk,
        input rst_n,
        input enable,               // 播放使能

        // 配置参数
        input [7:0] seq_length,     // 序列长度（1-256）
        input [31:0] freq_word,     // DDS频率字

        // RAM接口
        input [7:0] ram_data,       // RAM读出的数据
        output reg [7:0] rd_addr,   // RAM读地址

        // 序列输出
        output [7:0] seq_out        // 8通道输出
    );

    //=========================================================================
    // DDS相位累加器（生成播放时钟）
    //=========================================================================
    reg [31:0] phase_acc;       // 相位累加器
    reg phase_carry;            // 相位进位（播放时钟）

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase_acc <= 32'h0;
            phase_carry <= 1'b0;
        end
        else if (enable) begin
            // 检测溢出：如果累加后小于原值，说明发生了溢出
            phase_carry <= (phase_acc + freq_word) < phase_acc;
            phase_acc <= phase_acc + freq_word;
        end
        else begin
            phase_acc <= 32'h0;
            phase_carry <= 1'b0;
        end
    end

    //=========================================================================
    // 地址生成器（添加测试计数器）
    //=========================================================================
    reg [23:0] test_counter;  // 测试用慢速计数器

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rd_addr <= 8'h0;
            test_counter <= 24'h0;
        end
        else if (!enable) begin
            rd_addr <= 8'h0;
            test_counter <= 24'h0;
        end
        else begin
            // DDS相位累加器产生进位时切换地址
            if (phase_carry) begin
                if (rd_addr >= seq_length - 1)
                    rd_addr <= 8'h0;
                else
                    rd_addr <= rd_addr + 1;
            end

            // 旧的测试模式（固定1ms周期，已禁用）
            // test_counter <= test_counter + 1;
            // if (test_counter >= 24'd50000) begin
            //     test_counter <= 24'h0;
            //     if (rd_addr >= seq_length - 1)
            //         rd_addr <= 8'h0;
            //     else
            //         rd_addr <= rd_addr + 1;
            // end
        end
    end

    //=========================================================================
    // 输出数据寄存器（补偿RAM读延迟）
    //=========================================================================
    reg [7:0] seq_out_reg;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            seq_out_reg <= 8'h0;
        else if (enable)
            seq_out_reg <= ram_data;
        else
            seq_out_reg <= 8'h0;
    end

    // 输出：如果enable=1，输出寄存器数据；否则输出0
    assign seq_out = seq_out_reg;  // 简化：直接输出，方便调试

endmodule
