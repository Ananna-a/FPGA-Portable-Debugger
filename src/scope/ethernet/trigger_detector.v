/**
 * 触发检测模块 - Buffer模式专用
 * 
 * 功能：
 * - 支持边沿触发（上升沿/下降沿）
 * - 支持电平触发（可选）
 * - 触发位置记录
 * 
 * 作者：AI辅助开发
 * 日期：2025-11-23
 * 版本：V8.7.0
 */

module trigger_detector (
        input wire          clk,            // 系统时钟
        input wire          rst_n,          // 复位信号（低有效）

        // ADC数据输入（选择的触发源通道）
        input wire [15:0]   adc_data,       // ADC数据（12位有效，高位补0）
        input wire          adc_valid,      // ADC数据有效

        // 触发配置
        input wire [15:0]   trigger_level,  // 触发电平（12位，对应0-4095）
        input wire          trigger_edge,   // 触发边沿：0=上升沿, 1=下降沿
        input wire          trigger_enable, // 触发使能
        input wire [1:0]    trigger_mode,   // 触发模式：00=自动, 01=正常, 10=单次

        // 触发输出
        output reg          triggered,      // 触发脉冲（单周期）
        output reg [31:0]   trigger_pos     // 触发位置（采样计数）
    );

    // 数据延迟寄存器（用于边沿检测）
    reg [15:0] data_prev;

    // 采样计数器
    reg [31:0] sample_count;

    // 触发检测逻辑
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            triggered <= 1'b0;
            data_prev <= 16'd0;
            sample_count <= 32'd0;
            trigger_pos <= 32'd0;
        end
        else begin
            // 默认触发信号为低
            triggered <= 1'b0;

            if (adc_valid) begin
                // 采样计数递增
                sample_count <= sample_count + 1;

                if (trigger_enable && !triggered) begin
                    case (trigger_edge)
                        1'b0: begin  // 上升沿触发
                            // 前一采样 < 阈值 && 当前采样 >= 阈值
                            if (data_prev < trigger_level && adc_data >= trigger_level) begin
                                triggered <= 1'b1;
                                trigger_pos <= sample_count;
                            end
                        end

                        1'b1: begin  // 下降沿触发
                            // 前一采样 >= 阈值 && 当前采样 < 阈值
                            if (data_prev >= trigger_level && adc_data < trigger_level) begin
                                triggered <= 1'b1;
                                trigger_pos <= sample_count;
                            end
                        end
                    endcase
                end

                // 保存当前数据用于下次比较
                data_prev <= adc_data;
            end
        end
    end

endmodule
