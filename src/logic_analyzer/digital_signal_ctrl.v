//=============================================================================
// 8路数字信号分析控制器
// 功能：接收命令、管理8路测量、通过CH340返回结果
// 命令：0x66 开始测量（全部8路）
//       0x67 停止测量
//       0x68 读取指定通道结果 [channel]
// 日期：2025-11-06
//=============================================================================

module digital_signal_ctrl (
        input wire clk,              // 系统时钟 (50MHz) - 改用50MHz简化跨时钟域
        input wire rst_n,

        // 输入信号 (8路数字信号)
        input wire [7:0] signal_in,

        // 命令解析器接口
        input wire [7:0] cmd,
        input wire [7:0] payload_data,
        input wire payload_valid,
        input wire cmd_done,
        input wire cmd_valid_pulse,

        // 测量结果输出（并行）- 供debugger_top读取
        output reg [2:0] result_channel,     // 当前结果通道号
        output reg [31:0] result_freq,       // 频率
        output reg [31:0] result_high_cycles,// 高电平周期
        output reg [31:0] result_low_cycles, // 低电平周期
        output reg result_valid,             // 结果有效标志（单周期脉冲）

        // 🔥 新增：命令完成信号（参照DS18B20）
        output reg cmd_done_out,  // 命令处理完成（用于触发debugger_top的cmd_finish）

        // 🔥 V8.8.0新增：测量状态输出（解决与ADC采集的冲突）
        output wire dsa_measuring  // DSA测量中标志（任一通道在测量时为1）
    );

    //=========================================================================
    // 命令码定义
    //=========================================================================
    localparam CMD_DSA_START = 8'h66;    // 开始测量（全部8路）
    localparam CMD_DSA_STOP  = 8'h67;    // 停止测量
    localparam CMD_DSA_READ  = 8'h68;    // 读取指定通道结果

    //=========================================================================
    // 状态机定义
    //=========================================================================
    localparam IDLE         = 4'd0;
    localparam MEASURING    = 4'd1;
    localparam WAIT_PAYLOAD = 4'd2;  // 🔥新增：等待payload状态
    localparam PREP_DATA    = 4'd3;  // 准备数据

    //=========================================================================
    // 信号定义
    //=========================================================================
    reg [3:0] state;
    reg [2:0] current_ch;        // 当前测量/发送的通道

    // 8路测量器的控制和结果
    reg [7:0] measure_start;     // 每个通道的启动信号
    reg measure_stop_all;
    wire [7:0] meas_done;
    wire [7:0] measuring;

    wire [31:0] freq_hz[7:0];
    wire [31:0] high_time_cycles[7:0];  // 高电平时钟周期数
    wire [31:0] low_time_cycles[7:0];   // 低电平时钟周期数

    // 门控时间：1秒 = 50_000_000 时钟周期（50MHz）
    reg [31:0] gate_time;

    //=========================================================================
    // 实例化8个数字信号分析器
    //=========================================================================
    genvar i;
    generate
        for (i = 0; i < 8; i = i + 1) begin : DSA_CH
            digital_signal_analyzer u_dsa (
                                        .clk(clk),
                                        .rst_n(rst_n),
                                        .signal_in(signal_in),
                                        .measure_start(measure_start[i]),
                                        .measure_stop(measure_stop_all),
                                        .channel_sel(i[2:0]),
                                        .gate_time(gate_time),
                                        .freq_hz(freq_hz[i]),
                                        .high_time_cycles(high_time_cycles[i]),
                                        .low_time_cycles(low_time_cycles[i]),
                                        .meas_done(meas_done[i]),
                                        .measuring(measuring[i])
                                    );
        end
    endgenerate

    //=========================================================================
    // 🔥 V8.8.0: 测量状态输出（或运算所有通道的measuring信号）
    //=========================================================================
    assign dsa_measuring = |measuring;  // 任一通道在测量时为高电平

    //=========================================================================
    // 命令处理状态机
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            current_ch <= 3'd0;
            measure_start <= 8'h00;
            measure_stop_all <= 1'b0;
            gate_time <= 32'd50_000_000;  // 默认1秒 @ 50MHz
            result_channel <= 3'd0;
            result_freq <= 32'd0;
            result_high_cycles <= 32'd0;
            result_low_cycles <= 32'd0;
            result_valid <= 1'b0;
            cmd_done_out <= 1'b0;  // 🔥 初始化cmd_done_out
        end
        else begin
            // 默认清除单周期信号
            measure_start <= 8'h00;
            measure_stop_all <= 1'b0;
            result_valid <= 1'b0;
            cmd_done_out <= 1'b0;  // 🔥 默认清除

            // Payload接收（读取通道号） - 在任何状态下都可以接收
            if (cmd == CMD_DSA_READ && payload_valid) begin
                current_ch <= payload_data[2:0];  // 取低3位作为通道号
            end

            case (state)
                //-------------------------------------------------------------
                // 空闲状态：等待命令
                //-------------------------------------------------------------
                IDLE: begin
                    if (cmd_valid_pulse) begin
                        case (cmd)
                            //-----------------------------------------------
                            // 0x66: 开始测量（全部8路）
                            //-----------------------------------------------
                            CMD_DSA_START: begin
                                // 同时启动8路测量
                                measure_start <= 8'hFF;
                                state <= MEASURING;

                                // 🔥 发送cmd_done（不发送应答帧，由状态机自己发）
                                cmd_done_out <= 1'b1;
                            end

                            //-----------------------------------------------
                            // 0x67: 停止测量
                            //-----------------------------------------------
                            CMD_DSA_STOP: begin
                                measure_stop_all <= 1'b1;
                                state <= IDLE;

                                // 🔥 发送cmd_done
                                cmd_done_out <= 1'b1;
                            end

                            //-----------------------------------------------
                            // 0x68: 读取指定通道结果
                            //-----------------------------------------------
                            CMD_DSA_READ: begin
                                // 🔥修复：等待payload接收
                                state <= WAIT_PAYLOAD;
                            end

                            default: begin
                                // 未知命令，保持空闲
                            end
                        endcase
                    end
                end

                //-------------------------------------------------------------
                // 等待Payload：等待通道号接收完成
                //-------------------------------------------------------------
                WAIT_PAYLOAD: begin
                    // 等待cmd_done（表示payload接收完成）
                    if (cmd_done) begin
                        state <= PREP_DATA;
                    end
                end

                //-------------------------------------------------------------
                // 测量中：等待全部8路完成
                //-------------------------------------------------------------
                MEASURING: begin
                    // 检查是否全部完成
                    if (meas_done == 8'hFF || measure_stop_all) begin
                        state <= IDLE;
                    end
                end

                //-------------------------------------------------------------
                // 准备数据：输出测量结果
                //-------------------------------------------------------------
                PREP_DATA: begin
                    // 🔥 不再发送应答帧，只输出结果并通知debugger_top

                    // 输出测量结果
                    result_channel <= current_ch;
                    result_freq <= freq_hz[current_ch];
                    result_high_cycles <= high_time_cycles[current_ch];
                    result_low_cycles <= low_time_cycles[current_ch];
                    result_valid <= 1'b1;  // 通知debugger_top结果准备好（触发状态机）

                    cmd_done_out <= 1'b1;  // 🔥 命令处理完成

                    state <= IDLE;
                end

                //-------------------------------------------------------------
                // 默认
                //-------------------------------------------------------------
                default: begin
                    state <= IDLE;
                end
            endcase
        end
    end

endmodule
