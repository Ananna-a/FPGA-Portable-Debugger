//=============================================================================
// 频率计数器模块 (参考版本 - 已验证可用)
// 功能：测量输入信号的频率（Hz）
// 方法：在门控时间内统计上升沿数量
// 来源：src/参考/频率测量/frequency_counter.v
// 日期：2025-11-04
//=============================================================================

module frequency_counter (
        input wire clk,              // 系统时钟 (50MHz)
        input wire rst_n,

        // 输入信号
        input wire signal_in,        // 待测信号（数字方波）
        input wire signal_valid,     // 信号有效标志

        // 控制接口
        input wire measure_start,    // 测量启动信号（单周期脉冲）
        input wire [31:0] gate_time, // 门控时间（时钟周期数）

        // 输出接口
        output reg [31:0] freq_out,  // 测得频率 (Hz)
        output reg freq_valid,       // 频率有效标志（单周期脉冲）
        output reg measuring         // 测量进行中标志
    );

    //=========================================================================
    // 参数定义
    //=========================================================================
    localparam IDLE     = 2'b00;
    localparam COUNTING = 2'b01;
    localparam DONE     = 2'b10;

    //=========================================================================
    // 信号定义
    //=========================================================================
    reg [1:0] state;
    reg [31:0] edge_counter;     // 边沿计数器
    reg [31:0] gate_counter;     // 门控时间计数器
    reg signal_in_d1, signal_in_d2;
    wire posedge_detected;

    //=========================================================================
    // 上升沿检测
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            signal_in_d1 <= 1'b0;
            signal_in_d2 <= 1'b0;
        end
        else begin
            if (signal_valid) begin
                signal_in_d1 <= signal_in;
                signal_in_d2 <= signal_in_d1;
            end
        end
    end

    assign posedge_detected = signal_in_d1 & ~signal_in_d2;

    //=========================================================================
    // 频率测量状态机
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            edge_counter <= 32'd0;
            gate_counter <= 32'd0;
            freq_out <= 32'd0;
            freq_valid <= 1'b0;
            measuring <= 1'b0;
        end
        else begin
            case (state)
                IDLE: begin
                    freq_valid <= 1'b0;
                    measuring <= 1'b0;

                    if (measure_start) begin
                        edge_counter <= 32'd0;
                        gate_counter <= 32'd0;
                        state <= COUNTING;
                        measuring <= 1'b1;
                    end
                end

                COUNTING: begin
                    measuring <= 1'b1;

                    if (gate_counter < gate_time) begin
                        gate_counter <= gate_counter + 32'd1;

                        // 统计上升沿
                        if (signal_valid && posedge_detected) begin
                            edge_counter <= edge_counter + 32'd1;
                        end
                    end
                    else begin
                        // 门控时间到，计算频率
                        // 频率 = 边沿数 × 系统时钟频率 / 门控时间
                        // 简化：如果gate_time = 50MHz（1秒），则边沿数 = 频率
                        freq_out <= edge_counter;
                        freq_valid <= 1'b1;
                        state <= DONE;
                    end
                end

                DONE: begin
                    freq_valid <= 1'b0;
                    measuring <= 1'b0;
                    state <= IDLE;
                end

                default: begin
                    state <= IDLE;
                end
            endcase
        end
    end

endmodule
