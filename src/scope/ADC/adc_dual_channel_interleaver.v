//=============================================================================
// 双通道ADC数据交织器
// 功能：将两个独立ADC通道的数据交织打包
// 模式：CH1 CH2 CH1 CH2 ... 交替输出
//
// 设计说明：
//   - 两个ADC独立采样（同步时钟）
//   - 输出16位数据：[CH2_data[7:0], CH1_data[7:0]]
//   - 供DDR3存储和以太网发送
//
// 作者：AI辅助开发
// 日期：2025-11-19
//=============================================================================

module adc_dual_channel_interleaver(
        input  wire         clk,            // 系统时钟50MHz
        input  wire         rst_n,          // 复位信号

        // 🔥 新增：通道使能控制（硬件级）
        input  wire         ch1_enable,     // CH1采集使能
        input  wire         ch2_enable,     // CH2采集使能

        // 通道1输入（来自adc_capture_stream实例1）
        input  wire [7:0]   ch1_data,
        input  wire         ch1_valid,

        // 通道2输入（来自adc_capture_stream实例2）
        input  wire [7:0]   ch2_data,
        input  wire         ch2_valid,

        // 背压控制
        input  wire         fifo_full,

        // 16位交织输出（供adc_8bit_to_16bit或直接DDR3）
        output reg  [15:0]  interleaved_data,
        output reg          interleaved_valid
    );

    //=========================================================================
    // 双通道数据缓存 - V8.6.29修复版（恢复同步模式+修复清除时序）
    //=========================================================================
    reg [7:0] ch1_buffer;
    reg [7:0] ch2_buffer;
    reg ch1_ready;
    reg ch2_ready;

    // 🔥 V8.6.29: 恢复同步交织模式，但修复清除时序BUG
    //
    // 关键发现：
    //   - 低频段双通道工作正常 → 说明同步模式本身没问题
    //   - 高频段出现跳变 → 是V8.6.28引入的BUG：ready在输出前就被清除
    //
    // V8.6.28的错误：
    //   else if (ch1_ready && !fifo_full) begin
    //       ch1_ready <= 1'b0;  // ❌ 无条件清除，导致数据丢失！
    //   end
    //
    // 正确策略：
    //   - 双通道：必须同时ready才清除（确保严格同步）
    //   - 单通道：立即清除（不等待）
    //   - 清除条件：在输出的同一时钟周期

    wire clear_both;
    assign clear_both = (ch1_enable && ch2_enable) ?
           (ch1_ready && ch2_ready && !fifo_full) :  // 双通道同步清除
           1'b0;

    // 锁存CH1数据
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ch1_buffer <= 8'd0;
            ch1_ready <= 1'b0;
        end
        else if (!ch1_enable) begin
            ch1_ready <= 1'b0;
        end
        else if (clear_both) begin
            // 双通道模式：同步清除
            ch1_ready <= 1'b0;
        end
        else if (ch1_ready && !ch2_enable && !fifo_full) begin
            // 单CH1模式：立即清除
            ch1_ready <= 1'b0;
        end
        else if (ch1_valid && !ch1_ready && !fifo_full) begin
            // 锁存新数据（防覆盖）
            ch1_buffer <= ch1_data;
            ch1_ready <= 1'b1;
        end
    end

    // 锁存CH2数据
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ch2_buffer <= 8'd128;
            ch2_ready <= 1'b0;
        end
        else if (!ch2_enable) begin
            ch2_ready <= 1'b0;
        end
        else if (clear_both) begin
            // 双通道模式：同步清除
            ch2_ready <= 1'b0;
        end
        else if (ch2_ready && !ch1_enable && !fifo_full) begin
            // 单CH2模式：立即清除
            ch2_ready <= 1'b0;
        end
        else if (ch2_valid && !ch2_ready && !fifo_full) begin
            // 锁存新数据（防覆盖）
            ch2_buffer <= ch2_data;
            ch2_ready <= 1'b1;
        end
    end    //=========================================================================
    // 交织输出逻辑 - V8.6.29恢复同步模式
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            interleaved_data <= 16'd0;
            interleaved_valid <= 1'b0;
        end
        else begin
            if (!fifo_full) begin
                if (ch1_enable && ch2_enable) begin
                    // 🔥 双通道模式：严格同步交织
                    if (ch1_ready && ch2_ready) begin
                        interleaved_data <= {ch2_buffer, ch1_buffer};
                        interleaved_valid <= 1'b1;
                    end
                    else begin
                        interleaved_valid <= 1'b0;
                    end
                end
                else if (ch1_enable && !ch2_enable) begin
                    // 单CH1模式
                    if (ch1_ready) begin
                        interleaved_data <= {ch1_buffer, ch1_buffer};
                        interleaved_valid <= 1'b1;
                    end
                    else begin
                        interleaved_valid <= 1'b0;
                    end
                end
                else if (!ch1_enable && ch2_enable) begin
                    // 单CH2模式
                    if (ch2_ready) begin
                        interleaved_data <= {ch2_buffer, ch2_buffer};
                        interleaved_valid <= 1'b1;
                    end
                    else begin
                        interleaved_valid <= 1'b0;
                    end
                end
                else begin
                    interleaved_valid <= 1'b0;
                end
            end
            else begin
                interleaved_valid <= 1'b0;
            end
        end
    end

endmodule
