//=============================================================================
// PWM生成器模块 (基于DDS相位累加器原理)
// 功能：
//   - 单通道PWM波形生成
//   - 频率可调: 1Hz - 1MHz (DDS频率字控制)
//   - 占空比可调: 0-100% (16位精度, 0.0015%步进)
// 设计思路：
//   - 使用DDS相位累加器原理（参考DDS_Module_Dual脉冲波形生成）
//   - 频率字 = (freq_hz * 2^32) / clk_freq
//   - 占空比阈值 = (duty_cycle * 2^32) / 65536
//   - 比较相位累加器与阈值，输出高/低电平
// 优势：
//   - 无需除法运算，综合速度快
//   - 只需加法器和比较器
//   - 频率和占空比独立控制
// 毛刺消除：
//   - 三级流水线设计 + 参数锁存，彻底消除组合逻辑毛刺
//   - 第一级：寄存phase_acc和duty_threshold
//   - 第二级：寄存比较结果
//   - 第三级：输出寄存
//=============================================================================

module pwm_generator(
        input clk,                  // 系统时钟 (50MHz)
        input rst_n,
        input enable,               // 使能信号
        input [31:0] freq_word,     // DDS频率字 (由上层模块预计算)
        input [15:0] duty_cycle,    // 占空比 (0-65535, 对应0-100%)
        input config_update,        // 上层模块触发参数更新（防止异步变化）
        output reg pwm_out          // PWM输出
    );

    //=========================================================================
    // DDS相位累加器
    //=========================================================================
    reg [31:0] phase_acc;           // 相位累加器
    reg [31:0] duty_threshold;      // 占空比阈值

    // 流水线寄存器（消除毛刺）
    reg [31:0] phase_acc_r;         // 相位累加器打一拍
    reg [31:0] duty_threshold_r;    // 占空比阈值打一拍
    reg compare_result;              // 比较结果打一拍

    // 新增：锁存当前 duty_cycle，避免频繁变化
    reg [15:0] duty_cycle_latched;

    //=========================================================================
    // 占空比阈值计算
    // duty_threshold = (duty_cycle * 2^32) / 65536 = duty_cycle << 16
    // ⚠️ 默认值设为 0，避免复位后出现 50% 的“假信号”
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            duty_threshold <= 32'd0;
            duty_cycle_latched <= 16'd0;
        end
        else if (config_update) begin
            duty_cycle_latched <= duty_cycle;
            duty_threshold <= {duty_cycle_latched, 16'd0};  // 左移16位
        end
    end

    //=========================================================================
    // 相位累加器 (参考DDS模块)
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            phase_acc <= 32'd0;
        else if (!enable)
            phase_acc <= 32'd0;
        else
            phase_acc <= phase_acc + freq_word;
    end

    //=========================================================================
    // 流水线第一级：寄存输入数据
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase_acc_r <= 32'd0;
            duty_threshold_r <= 32'd0;
        end
        else begin
            phase_acc_r <= phase_acc;
            duty_threshold_r <= duty_threshold;
        end
    end

    //=========================================================================
    // 流水线第二级：比较操作
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            compare_result <= 1'b0;
        else if (!enable)
            compare_result <= 1'b0;
        else
            compare_result <= (phase_acc_r < duty_threshold_r);
    end

    //=========================================================================
    // 流水线第三级：输出寄存
    // ⚠️ 通过三级流水线 + 参数锁存，彻底消除组合逻辑毛刺
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            pwm_out <= 1'b0;
        else if (!enable)
            pwm_out <= 1'b0;
        else
            pwm_out <= compare_result;
    end

endmodule
