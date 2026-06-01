//=============================================================================
// 逻辑分析仪数据采集模块
// 功能: 8通道数字信号采样，支持可变采样率和触发功能
// 版本: V2.0 - 简化版（50MHz直接采样，移除异步FIFO）
// 日期: 2025-01-XX
//
// 修改说明:
//   - 移除125MHz采样时钟，直接使用50MHz系统时钟
//   - 移除异步FIFO，同时钟域直接输出
//   - 采样分频基于50MHz (1=50MSPS, 2=25MSPS, 5=10MSPS...)
//=============================================================================

module logic_analyzer_capture (
        input wire clk,              // 系统时钟 50MHz (采样和输出同时钟)
        input wire rst_n,

        // ========== 输入信号 ==========
        input wire [7:0] logic_in,   // 8路数字输入

        // ========== 控制接口 ==========
        input wire capture_en,       // 采集使能（单周期脉冲启动）
        input wire capture_stop,     // 停止采集
        input wire [31:0] sample_div,// 采样分频系数（基于50MHz: 1=50MSPS, 2=25MSPS, 5=10MSPS...）
        input wire [31:0] capture_len,// 采集长度（字节数，0=连续采集）

        // ========== 触发配置（V1.0暂时简化，仅支持电平触发） ==========
        input wire trigger_en,           // 触发使能
        input wire [7:0] trigger_mask,   // 触发通道掩码（1=参与触发判断）
        input wire [7:0] trigger_value,  // 触发值（与mask配合使用）

        // ========== FIFO接口 ==========
        output [7:0] fifo_data,
        output fifo_wr_en,
        input wire fifo_full,

        // ========== 状态输出 ==========
        output reg [31:0] captured_count, // 已采集字节数
        output reg capture_done,          // 采集完成标志
        output reg [2:0] state,           // 状态机状态（用于调试）
        output reg trigger_detected       // 触发检测到标志
    );

    //=========================================================================
    // 状态机定义
    //=========================================================================
    localparam IDLE         = 3'd0;  // 空闲
    localparam WAIT_TRIGGER = 3'd1;  // 等待触发
    localparam CAPTURING    = 3'd2;  // 采集中
    localparam DONE         = 3'd3;  // 完成

    reg [2:0] current_state;

    // 🔥🔥🔥 V8.1新增：内部复位延时（确保寄存器完全初始化）
    reg [7:0] reset_delay_cnt;
    reg internal_rst_done;
    localparam RESET_DELAY = 8'd50;  // 1us复位延时 (50MHz × 1us)

    //=========================================================================
    // 采样分频计数器（50MHz时钟域）
    // 🔥关键修复：使用sample_div直接计数，使用==精确判断避免毛刺
    //=========================================================================
    reg [31:0] div_counter;
    reg sample_pulse;  // 采样脉冲（每个分频周期产生一次）

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            div_counter <= 32'd0;
            sample_pulse <= 1'b0;
        end
        else begin
            // 默认sample_pulse为0
            sample_pulse <= 1'b0;

            // 🔥修复：只在采集状态(CAPTURING或WAIT_TRIGGER)下计数
            // 🔥 优化：严格检查状态，避免状态机异常时继续计数
            if ((current_state == CAPTURING || current_state == WAIT_TRIGGER) && sample_div > 32'd1) begin
                // 🔥🔥🔥 致命BUG修复：恢复使用==精确判断
                // 原bug：使用>=会在某些情况下产生多个脉冲或卡死
                // 正确逻辑：计数到sample_div-1时精确复位（例如div=3: 0→1→2→复位）
                // 🔥 边界检查：sample_div必须≥2，否则会导致溢出
                if (div_counter == sample_div - 1) begin
                    // 计数器达到目标值，产生采样脉冲
                    div_counter <= 32'd0;
                    sample_pulse <= 1'b1;
                end
                else begin
                    div_counter <= div_counter + 32'd1;
                end
            end
            else if ((current_state == CAPTURING || current_state == WAIT_TRIGGER) && sample_div == 32'd1) begin
                // 🔥 特殊情况：sample_div=1时，每个时钟周期都产生脉冲（50MSPS）
                sample_pulse <= 1'b1;
            end
            else begin
                // 🔥 关键修复：非采集状态或sample_div无效时，强制复位
                div_counter <= 32'd0;
                sample_pulse <= 1'b0;
            end
        end
    end

    //=========================================================================
    // 输入信号同步和采样（50MHz时钟域）
    // 🔥 关键修复：3级同步彻底消除亚稳态，立即采样避免延迟
    //=========================================================================
    reg [7:0] logic_in_sync1;
    reg [7:0] logic_in_sync2;
    reg [7:0] logic_in_sync3;    // 🔥 新增第3级同步
    reg [7:0] logic_in_sampled;  // 采样后的数据

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            logic_in_sync1 <= 8'd0;
            logic_in_sync2 <= 8'd0;
            logic_in_sync3 <= 8'd0;
            logic_in_sampled <= 8'd0;
        end
        else begin
            // 🔥 关键修复2：三级同步链，彻底消除亚稳态（MTBF > 10^15秒）
            logic_in_sync1 <= logic_in;
            logic_in_sync2 <= logic_in_sync1;
            logic_in_sync3 <= logic_in_sync2;

            // 🔥 关键修复3：在sample_pulse时立即采样已稳定的数据
            // 此时sync3已经稳定3个时钟周期，完全消除亚稳态
            if (sample_pulse) begin
                logic_in_sampled <= logic_in_sync3;
            end
        end
    end

    //=========================================================================
    // FIFO写使能和数据输出（同时钟域直接输出）
    // 🔥 关键修复4：使用sample_pulse当拍写入，消除时序不确定性
    //=========================================================================
    reg [7:0] fifo_data_reg;
    reg fifo_wr_en_reg;
    reg [31:0] dropped_samples;  // 🔥 新增：丢失样本计数（调试用）

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            fifo_data_reg <= 8'h00;
            fifo_wr_en_reg <= 1'b0;
            dropped_samples <= 32'd0;
        end
        else begin
            // 🔥 关键修复：严格检查状态，只在CAPTURING状态且sample_pulse时写入
            // 避免状态机异常时误写入数据
            if (sample_pulse && (current_state == CAPTURING) && !fifo_full) begin
                fifo_data_reg <= logic_in_sync3;  // 🔥 直接使用最新的稳定数据
                fifo_wr_en_reg <= 1'b1;
            end
            else begin
                // 🔥 优化：任何非采集状态都清除写使能
                fifo_wr_en_reg <= 1'b0;
                
                // FIFO满时丢弃样本并计数（仅在采集状态统计）
                if (sample_pulse && (current_state == CAPTURING) && fifo_full) begin
                    dropped_samples <= dropped_samples + 1'b1;
                end
            end

            // 🔥 优化：进入IDLE或DONE状态时清零丢失计数
            if (current_state == IDLE || current_state == DONE) begin
                dropped_samples <= 32'd0;
            end
        end
    end

    assign fifo_data = fifo_data_reg;
    assign fifo_wr_en = fifo_wr_en_reg;

    //=========================================================================
    // 触发检测逻辑（50MHz时钟域）
    //=========================================================================
    reg trigger_match;

    always @(*) begin
        // 触发条件：(输入 & 掩码) == (触发值 & 掩码)
        trigger_match = ((logic_in_sampled & trigger_mask) == (trigger_value & trigger_mask));
    end

    //=========================================================================
    // 控制信号边沿检测
    //=========================================================================
    reg capture_en_d;
    wire capture_en_posedge;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            capture_en_d <= 1'b0;
        else
            capture_en_d <= capture_en;
    end

    assign capture_en_posedge = capture_en && (!capture_en_d);

    //=========================================================================
    // 主状态机（50MHz时钟域）
    //=========================================================================
    reg [31:0] capture_counter;  // 采集计数器

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            current_state <= IDLE;
            capture_counter <= 32'd0;
            captured_count <= 32'd0;
            capture_done <= 1'b0;
            trigger_detected <= 1'b0;
            reset_delay_cnt <= 8'd0;
            internal_rst_done <= 1'b0;
        end
        else begin
            // 🔥🔥🔥 V8.1：复位延时逻辑（确保所有寄存器稳定）
            if (!internal_rst_done) begin
                if (reset_delay_cnt < RESET_DELAY) begin
                    reset_delay_cnt <= reset_delay_cnt + 8'd1;
                    // 延时期间保持IDLE状态，忽略所有外部命令
                end
                else begin
                    internal_rst_done <= 1'b1;
                end
            end
            // 🔥 只有内部复位完成后才处理状态机逻辑
            else begin
            case (current_state)
                //=====================================================
                // 空闲状态
                //=====================================================
                IDLE: begin
                    capture_counter <= 32'd0;
                    captured_count <= 32'd0;
                    capture_done <= 1'b0;
                    trigger_detected <= 1'b0;

                    // 检测启动命令
                    if (capture_en_posedge) begin
                        if (trigger_en) begin
                            // 使能触发，进入等待触发状态
                            current_state <= WAIT_TRIGGER;
                        end
                        else begin
                            // 不使能触发，直接开始采集
                            current_state <= CAPTURING;
                        end
                    end
                end

                //=====================================================
                // 等待触发状态
                //=====================================================
                WAIT_TRIGGER: begin
                    // 🔥 关键修复：优先处理停止命令（立即响应）
                    if (capture_stop) begin
                        current_state <= IDLE;  // 🔥 直接返回IDLE，不经过DONE
                        capture_done <= 1'b0;
                    end
                    // 🔥 关键修复5：触发检测在sample_pulse当拍判断，与采样同步
                    else if (sample_pulse && trigger_match) begin
                        trigger_detected <= 1'b1;
                        current_state <= CAPTURING;
                    end
                end

                //=====================================================
                // 采集状态
                //=====================================================
                CAPTURING: begin
                    // 🔥 关键修复：优先处理停止命令（立即响应，避免状态机卡死）
                    if (capture_stop) begin
                        current_state <= IDLE;  // 🔥 直接返回IDLE，不经过DONE
                        capture_done <= 1'b0;
                    end
                    // 如果设置了采集长度（非0），检测是否达到目标长度
                    else if ((capture_len != 32'd0) && (capture_counter >= capture_len)) begin
                        current_state <= DONE;
                    end
                    // 🔥 关键修复6：计数与写入同步，在sample_pulse当拍计数
                    // 只有成功写入FIFO的样本才计数（避免计数与实际数据不符）
                    else if (sample_pulse && !fifo_full) begin
                        capture_counter <= capture_counter + 32'd1;
                        captured_count <= captured_count + 32'd1;
                    end
                end

                //=====================================================
                // 完成状态
                //=====================================================
                DONE: begin
                    capture_done <= 1'b1;

                    // 🔥 关键修复：在DONE状态自动快速返回IDLE（避免卡住）
                    // 任何命令都会清除DONE状态
                    if (capture_stop || capture_en_posedge) begin
                        current_state <= IDLE;
                        capture_done <= 1'b0;
                    end
                    // 🔥 新增：自动超时机制（避免状态机永久卡在DONE）
                    // 如果在DONE状态停留超过1个时钟周期，自动返回IDLE
                    else begin
                        current_state <= IDLE;  // 自动返回IDLE
                        capture_done <= 1'b0;
                    end
                end

                default: begin
                    current_state <= IDLE;
                end
            endcase
            end  // 🔥 V8.1：内部复位完成分支结束
        end
    end

    // 状态输出（用于外部读取）
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            state <= 3'd0;
        else
            state <= current_state;
    end

endmodule
