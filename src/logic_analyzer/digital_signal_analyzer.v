//=============================================================================
// 8路数字信号分析器模块（V2.0 - 模块化重构版）
// 功能：测量8路数字信号的频率、高电平时间、低电平时间、占空比
// 架构：例化frequency_counter模块，实现与ADC测频完全隔离
// 优势：
//   1. 代码复用，减少维护成本
//   2. 独立的测频实例，避免与ADC测频冲突
//   3. 统一的测频逻辑，行为一致性更好
// 日期：2025-11-27
//=============================================================================

module digital_signal_analyzer (
        input wire clk,              // 系统时钟 (50MHz)
        input wire rst_n,

        // 输入信号 (8路数字信号，来自逻辑分析仪通道)
        input wire [7:0] signal_in,

        // 控制接口
        input wire measure_start,    // 启动测量（单周期脉冲）
        input wire measure_stop,     // 停止测量
        input wire [2:0] channel_sel,// 选择要测量的通道 (0-7)
        input wire [31:0] gate_time, // 门控时间（系统时钟周期数，50_000_000 = 1秒@50MHz）

        // 输出接口（针对选中的通道）
        output reg [31:0] freq_hz,       // 频率 (Hz)
        output reg [31:0] high_time_cycles,  // 高电平时间 (时钟周期数，上位机需除以50得到微秒)
        output reg [31:0] low_time_cycles,   // 低电平时间 (时钟周期数，上位机需除以50得到微秒)
        output reg meas_done,            // 测量完成标志（单周期脉冲）
        output wire measuring            // 测量中标志（直接来自frequency_counter）
    );

    //=========================================================================
    // 参数定义
    //=========================================================================
    localparam IDLE     = 2'b00;
    localparam MEASURING = 2'b01;
    localparam DONE     = 2'b10;

    //=========================================================================
    // 信号定义
    //=========================================================================
    reg [1:0] state;
    reg [31:0] high_counter;     // 高电平时间计数器
    reg [31:0] low_counter;      // 低电平时间计数器
    reg [31:0] gate_counter;     // 门控时间计数器

    reg signal_d1, signal_d2;
    wire selected_signal;
    wire negedge_detected;

    // 🔥 V2.0新增：frequency_counter接口信号
    wire [31:0] freq_counter_out;
    wire freq_valid;
    wire freq_measuring;
    reg freq_start_pulse;

    //=========================================================================
    // 选择当前通道信号
    //=========================================================================
    assign selected_signal = signal_in[channel_sel];

    //=========================================================================
    // 🔥 V2.0核心改进：例化独立的frequency_counter模块
    // 说明：
    //   - 每个digital_signal_analyzer实例有自己的frequency_counter
    //   - 8个DSA实例 = 8个独立的frequency_counter
    //   - 与ADC的2个frequency_counter完全隔离，无资源竞争
    //=========================================================================
    frequency_counter u_freq_counter_dsa (
                          .clk              (clk),
                          .rst_n            (rst_n),

                          // 输入信号
                          .signal_in        (selected_signal),
                          .signal_valid     (1'b1),  // DSA信号始终有效

                          // 控制接口
                          .measure_start    (freq_start_pulse),  // 由状态机控制
                          .gate_time        (gate_time),

                          // 输出接口
                          .freq_out         (freq_counter_out),
                          .freq_valid       (freq_valid),
                          .measuring        (freq_measuring)
                      );

    // 对外暴露测量状态
    assign measuring = (state == MEASURING) || freq_measuring;

    //=========================================================================
    // 边沿检测（用于高低电平时间统计）
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            signal_d1 <= 1'b0;
            signal_d2 <= 1'b0;
        end
        else begin
            // 只在测量状态下采样信号
            if (state == MEASURING) begin
                signal_d1 <= selected_signal;
                signal_d2 <= signal_d1;
            end
        end
    end

    assign negedge_detected = ~signal_d1 & signal_d2;

    //=========================================================================
    // 测量状态机（V2.0：简化为协调frequency_counter + 高低电平统计）
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            high_counter <= 32'd0;
            low_counter <= 32'd0;
            gate_counter <= 32'd0;
            freq_hz <= 32'd0;
            high_time_cycles <= 32'd0;
            low_time_cycles <= 32'd0;
            meas_done <= 1'b0;
            freq_start_pulse <= 1'b0;
        end
        else begin
            // 默认清除单周期信号
            freq_start_pulse <= 1'b0;
            meas_done <= 1'b0;

            case (state)
                IDLE: begin
                    if (measure_start) begin
                        // 初始化计数器
                        high_counter <= 32'd0;
                        low_counter <= 32'd0;
                        gate_counter <= 32'd0;

                        // 启动frequency_counter
                        freq_start_pulse <= 1'b1;
                        state <= MEASURING;
                    end
                end

                MEASURING: begin
                    // 停止测量命令（提前终止）
                    if (measure_stop) begin
                        freq_hz <= freq_counter_out;  // 使用当前频率值
                        high_time_cycles <= high_counter;
                        low_time_cycles <= low_counter;
                        state <= DONE;
                    end
                    // 门控时间计数（与frequency_counter同步）
                    else if (gate_counter < gate_time) begin
                        gate_counter <= gate_counter + 32'd1;

                        // 统计高低电平时间（每个周期）
                        if (signal_d1) begin
                            high_counter <= high_counter + 32'd1;
                        end
                        else begin
                            low_counter <= low_counter + 32'd1;
                        end
                    end
                    // frequency_counter完成测量
                    else if (freq_valid) begin
                        // 保存测量结果
                        freq_hz <= freq_counter_out;
                        high_time_cycles <= high_counter;
                        low_time_cycles <= low_counter;
                        state <= DONE;
                    end
                end

                DONE: begin
                    meas_done <= 1'b1;
                    state <= IDLE;
                end

                default: begin
                    state <= IDLE;
                end
            endcase
        end
    end

endmodule
