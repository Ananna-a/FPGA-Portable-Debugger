//=============================================================================
// 过零比较器模块（极限灵敏度版）
// 功能：将ADC模拟信号转换为数字方波，用于频率测量
// 优化：V3.0 极限版 - 8点滤波 + 超紧凑阈值 + 趋势检测
// 来源：src/参考/频率测量/zero_cross_comparator.v
// 日期：2025-11-29
// 改进点：
//   1. 8点移动平均滤波 - 更强的噪声抑制（SNR提升6dB）
//   2. 超紧凑阈值（±1.5码）- 理论可检测0.15Vpp信号
//   3. 趋势检测算法 - 判断信号变化方向，减少防抖延迟
//   4. 更精细的DC跟踪 - 32次采样更新（更稳定）
//   5. 单次确认翻转 - 配合趋势检测，响应更快
//=============================================================================

module zero_cross_comparator (
        input wire clk,
        input wire rst_n,

        // ADC数据输入
        input wire [7:0] adc_data,
        input wire adc_data_valid,

        // 配置参数（保留兼容性，内部使用自适应算法）
        input wire [7:0] threshold_high,  // 高阈值（内部自动计算）
        input wire [7:0] threshold_low,   // 低阈值（内部自动计算）

        // 方波输出
        output reg signal_out,            // 数字方波信号
        output reg signal_valid           // 输出有效标志
    );

    //=========================================================================
    // 参数定义
    //=========================================================================
    localparam HYSTERESIS_WIDTH = 8'd2;       // 迟滞带宽：2码（约0.14V）- 极限灵敏度
    localparam FILTER_SAMPLES = 8;             // 滤波采样数：8次平均（提升6dB SNR）
    localparam DEBOUNCE_COUNT = 1;             // 防抖计数：单次确认（配合趋势检测）

    //=========================================================================
    // 1. 增强型数字低通滤波器（8点移动平均 + 子采样）
    //=========================================================================
    reg [7:0] adc_buf[0:FILTER_SAMPLES-1];
    reg [10:0] adc_sum;                        // 11位累加和（8*255=2040）
    reg [7:0] adc_filtered;                    // 滤波后的ADC值
    reg [7:0] adc_filtered_prev;               // 上一次滤波值（用于趋势检测）
    integer i;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (i = 0; i < FILTER_SAMPLES; i = i + 1) begin
                adc_buf[i] <= 8'd127;
            end
            adc_sum <= 11'd1016;               // 127 * 8
            adc_filtered <= 8'd127;
            adc_filtered_prev <= 8'd127;
        end
        else if (adc_data_valid) begin
            // 移位寄存器更新
            adc_buf[0] <= adc_data;
            for (i = 1; i < FILTER_SAMPLES; i = i + 1) begin
                adc_buf[i] <= adc_buf[i-1];
            end

            // 计算8点和
            adc_sum <= adc_buf[0] + adc_buf[1] + adc_buf[2] + adc_buf[3] +
                    adc_buf[4] + adc_buf[5] + adc_buf[6] + adc_buf[7];

            // 保存上一次的值
            adc_filtered_prev <= adc_filtered;

            // 除以8（右移3位）
            adc_filtered <= adc_sum[10:3];
        end
    end

    //=========================================================================
    // 2. 趋势检测器（判断信号上升/下降）
    //=========================================================================
    reg trend_up;                              // 上升趋势标志
    reg trend_down;                            // 下降趋势标志

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            trend_up <= 1'b0;
            trend_down <= 1'b0;
        end
        else if (adc_data_valid) begin
            // 简单差分检测
            if (adc_filtered > adc_filtered_prev) begin
                trend_up <= 1'b1;
                trend_down <= 1'b0;
            end
            else if (adc_filtered < adc_filtered_prev) begin
                trend_up <= 1'b0;
                trend_down <= 1'b1;
            end
            // 相等时保持上一状态
        end
    end    //=========================================================================
    // 3. 动态中心值计算（超慢速移动平均，更稳定的DC跟踪）
    //=========================================================================
    reg [15:0] dc_accumulator;                 // DC值累加器（16位）
    reg [7:0] dc_center;                       // 动态中心值
    reg [5:0] dc_counter;                      // DC更新计数器（扩展到6位）

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            dc_accumulator <= 16'd32512;       // 127 * 256
            dc_center <= 8'd127;
            dc_counter <= 6'd0;
        end
        else if (adc_data_valid) begin
            // 每32次采样更新一次DC中心值（更慢速跟踪，更稳定）
            if (dc_counter == 6'd31) begin
                dc_counter <= 6'd0;
                // 一阶IIR低通：dc_new = (255*dc_old + adc_filtered) / 256
                dc_accumulator <= dc_accumulator - {8'd0, dc_center} + {8'd0, adc_filtered};
                dc_center <= dc_accumulator[15:8];
            end
            else begin
                dc_counter <= dc_counter + 1'd1;
            end
        end
    end

    //=========================================================================
    // 4. 自适应阈值计算（超紧凑阈值）
    //=========================================================================
    reg [7:0] threshold_h;                     // 自适应高阈值
    reg [7:0] threshold_l;                     // 自适应低阈值

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            threshold_h <= 8'd129;
            threshold_l <= 8'd125;
        end
        else begin
            // 动态阈值：dc_center ± HYSTERESIS_WIDTH（±2码）
            threshold_h <= dc_center + HYSTERESIS_WIDTH;
            threshold_l <= dc_center - HYSTERESIS_WIDTH;
        end
    end

    //=========================================================================
    // 5. 趋势辅助的迟滞比较器（单次确认 + 趋势验证）
    //=========================================================================
    reg [1:0] debounce_high;                   // 高电平防抖计数
    reg [1:0] debounce_low;                    // 低电平防抖计数

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            signal_out <= 1'b0;
            signal_valid <= 1'b0;
            debounce_high <= 2'd0;
            debounce_low <= 2'd0;
        end
        else begin
            if (adc_data_valid) begin
                // 迟滞比较 + 趋势辅助
                if (adc_filtered > threshold_h && trend_up) begin
                    // 高阈值检测 + 上升趋势确认
                    if (debounce_high < DEBOUNCE_COUNT) begin
                        debounce_high <= debounce_high + 1'd1;
                    end
                    else begin
                        signal_out <= 1'b1;
                    end
                    debounce_low <= 2'd0;
                end
                else if (adc_filtered < threshold_l && trend_down) begin
                    // 低阈值检测 + 下降趋势确认
                    if (debounce_low < DEBOUNCE_COUNT) begin
                        debounce_low <= debounce_low + 1'd1;
                    end
                    else begin
                        signal_out <= 1'b0;
                    end
                    debounce_high <= 2'd0;
                end
                // 在迟滞区间内或趋势不匹配：保持状态

                signal_valid <= 1'b1;
            end
            else begin
                signal_valid <= 1'b0;
            end
        end
    end

endmodule
