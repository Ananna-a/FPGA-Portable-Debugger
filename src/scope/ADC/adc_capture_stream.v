//=============================================================================
// ADC单通道数据采集与流式传输模块 V3.1（支持动态采样率控制+50MSPS）
// 功能：
//   1. 流模式(Stream)：连续采集，实时传输，低延迟
//   2. Buffer模式(Single Shot)：单次触发采集，高采样率，可触发
//   3. V3.0新增：动态采样率控制（通过div_set参数）
//   4. V3.1修改：RESAMPLE_RATIO=1，基础采样率50MSPS，最高支持12MHz信号
// 作者：自动生成
// 日期：2025-11-04
// 版本：V3.1
//=============================================================================

module adc_capture_stream #(
        parameter RESAMPLE_RATIO = 1  // 基础重采样比率（1=无降采样，50MSPS）
    )(
        input  wire         sys_clk,        // 系统时钟（50MHz）
        input  wire         sys_rst_n,      // 系统复位，低电平有效

        // ADC硬件接口
        input  wire         adc_clk_180,    // ADC时钟输入（180°相位偏移，由内部PLL提供）
        input  wire [7:0]   adc_data_a,     // ADC通道A数据输入
        input  wire [7:0]   adc_data_b,     // ADC通道B数据输入（保留端口但未使用）
        output wire         adc_clk_out_a,  // ADC通道A时钟输出
        output wire         adc_clk_out_b,  // ADC通道B时钟输出（保留但未使用）

        // FIFO缓冲接口（写入侧，ADC时钟域）
        output reg  [7:0]   fifo_wr_data,   // 写入FIFO的数据
        output reg          fifo_wr_en,     // FIFO写使能
        input  wire         fifo_full,      // FIFO满标志（背压控制）

        // 基本状态输出
        output reg          stream_active,  // 数据流激活标志
        output reg [31:0]   sample_counter, // 采样计数器（调试用）

        //=========================================================================
        // V2.0 新增：模式控制接口
        //=========================================================================
        input  wire         mode_select,    // 0=流模式, 1=Buffer模式
        input  wire         capture_start,  // Buffer模式：采集启动命令（单周期脉冲）
        input  wire         capture_stop,   // 采集停止命令（单周期脉冲）
        input  wire [31:0]  capture_length, // Buffer模式：采样点数
        output reg          capture_done,   // Buffer模式：采集完成标志

        //=========================================================================
        // V2.0 新增：触发控制接口（流模式和Buffer模式均支持）
        //=========================================================================
        input  wire         trigger_en,     // 触发使能（流模式+Buffer模式均支持）
        input  wire [15:0]  trigger_level,  // 触发电平（16位）
        input  wire         trigger_edge,   // 触发边沿：0=上升沿, 1=下降沿
        output reg          trigger_detected, // 触发检测到标志

        //=========================================================================
        // V2.0 新增：状态输出
        //=========================================================================
        output reg [2:0]    adc_state,      // ADC状态机
        output reg          adc_busy,       // 采集忙标志

        //=========================================================================
        // V3.0 新增：动态采样率控制
        // V3.1 修改：基础采样率50MSPS (RESAMPLE_RATIO=1)
        //=========================================================================
        input  wire [31:0]  div_set         // 采样率分频设置（1=最快, 2=1/2速率...）
        // 实际采样率 = 50MHz / RESAMPLE_RATIO / div_set
        // 当RESAMPLE_RATIO=1时：实际采样率 = 50MHz / div_set
        // 最高采样率：50MSPS (div_set=1)
        // 最高支持信号频率：12MHz (保证至少4个采样点/周期)
    );

    //=========================================================================
    // 参数定义
    //=========================================================================
    localparam CH_A = 1'b0;
    localparam CH_B = 1'b1;

    // 计算分频计数器位宽
    localparam CNT_WIDTH = (RESAMPLE_RATIO == 1)  ? 1 :
               (RESAMPLE_RATIO <= 2)  ? 1 :
               (RESAMPLE_RATIO <= 4)  ? 2 :
               (RESAMPLE_RATIO <= 8)  ? 3 :
               (RESAMPLE_RATIO <= 16) ? 4 : 5;

    // 状态机定义
    localparam STATE_IDLE         = 3'd0;  // 空闲
    localparam STATE_WAIT_TRIGGER = 3'd1;  // 等待触发（Buffer模式+触发使能）
    localparam STATE_CAPTURING    = 3'd2;  // 采集中
    localparam STATE_DONE         = 3'd3;  // 完成（Buffer模式）

    //=========================================================================
    // ADC时钟输出连接
    //=========================================================================
    // 将内部PLL生成的180°相位时钟直接输出给ADC芯片
    assign adc_clk_out_a = adc_clk_180;
    assign adc_clk_out_b = adc_clk_180;

    //=========================================================================
    // V3.0：两级采样率控制
    // 第一级：RESAMPLE_RATIO - 固定基础降采样（例如4倍）
    // 第二级：div_set - 动态可调分频（软件控制）
    // 最终采样率 = 50MHz / RESAMPLE_RATIO / div_set
    //=========================================================================

    // 第一级：基础重采样分频计数器（固定）
    reg [CNT_WIDTH-1:0] resample_cnt;
    reg                 base_sample_en;  // 基础采样使能脉冲

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            resample_cnt <= {CNT_WIDTH{1'b0}};
            base_sample_en <= 1'b0;
        end
        else begin
            if (resample_cnt >= RESAMPLE_RATIO - 1) begin
                resample_cnt <= {CNT_WIDTH{1'b0}};
                base_sample_en <= 1'b1;  // 产生基础采样使能脉冲
            end
            else begin
                resample_cnt <= resample_cnt + 1'b1;
                base_sample_en <= 1'b0;
            end
        end
    end

    // 第二级：动态分频计数器（软件可调）
    // V3.2: 采样率分频参数锁存机制（避免采样过程中改变导致抖动）
    reg [31:0] div_set_latched;
    reg [31:0] div_cnt;
    reg        sample_en;        // 最终采样使能（结合两级分频）

    // 安全的div_set值（防止0或过小导致问题）
    wire [31:0] div_set_safe;
    assign div_set_safe = (div_set == 32'd0) ? 32'd1 : div_set;

    // V3.6: 关键修复 - 解决div=1时的锁存冲突问题
    // 问题：div=1时，div_cnt每周期都是0，导致每次都触发锁存，与计数逻辑冲突
    // 修复策略：
    //   1. IDLE状态：立即更新（参考SPI的!spi_busy时更新）
    //   2. 流模式运行中：使用延迟检测，避免与计数器清零冲突
    //   3. Buffer模式：保持当前值直到IDLE

    // 延迟一个周期的div_cnt（用于检测下降沿）
    reg [31:0] div_cnt_d1;
    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            div_cnt_d1 <= 32'd0;
        end
        else if (base_sample_en) begin
            div_cnt_d1 <= div_cnt;
        end
    end

    // 检测div_cnt的下降沿（从非0变为0）
    wire div_cnt_falling_edge;
    assign div_cnt_falling_edge = (div_cnt_d1 != 32'd0) && (div_cnt == 32'd0);

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            div_set_latched <= 32'd1;
        end
        else begin
            if (adc_state == STATE_IDLE) begin
                // ✅ IDLE状态：立即应用新配置（参考SPI空闲时更新）
                div_set_latched <= div_set_safe;
            end
            else if (mode_select == 1'b0) begin
                // ✅ V3.6修复：使用下降沿检测，避免div=1时的冲突
                // 当div_cnt从非0跳变到0时，说明一个完整周期结束，可以安全更新
                if (div_cnt_falling_edge && base_sample_en) begin
                    div_set_latched <= div_set_safe;
                end
            end
            // Buffer模式运行中：保持不变，避免影响采样完整性
        end
    end

    // V3.5: 关键修复 - 分频计数器应该每个base_sample_en都递增
    // 问题：之前的逻辑在IDLE时清零后，需要等待base_sample_en才开始计数
    //       导致设置采样率后状态机卡死
    // 修复：分频计数器持续工作，不受状态影响
    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            div_cnt <= 32'd0;
            sample_en <= 1'b0;
        end
        else begin
            // ⚡ 关键修复：移除IDLE清零逻辑，让计数器持续运行
            // 这样切换采样率时不会卡死
            if (base_sample_en) begin
                // 使用锁存的div_set值进行分频
                if (div_cnt >= div_set_latched - 1) begin
                    div_cnt <= 32'd0;
                    sample_en <= 1'b1;  // 产生采样脉冲
                end
                else begin
                    div_cnt <= div_cnt + 1'd1;
                    sample_en <= 1'b0;
                end
            end
            else begin
                sample_en <= 1'b0;  // base_sample_en=0时sample_en也=0
            end
        end
    end

    //=========================================================================
    // ADC数据锁存（在系统时钟域采样ADC数据）
    //=========================================================================
    reg [7:0] adc_a_reg;
    reg [7:0] adc_a_reg_d1;  // 用于触发检测的延迟数据

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            adc_a_reg <= 8'd0;
            adc_a_reg_d1 <= 8'd0;
        end
        else begin
            if (sample_en) begin
                adc_a_reg_d1 <= adc_a_reg;       // 保存前一次数据
                adc_a_reg <= adc_data_a;         // 锁存当前数据
            end
        end
    end

    //=========================================================================
    // 触发检测逻辑（仅Buffer模式+触发使能时有效）
    //=========================================================================
    reg trigger_armed;  // 触发准备好标志
    reg [15:0] adc_curr_16, adc_prev_16;  // 16位比较值

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            trigger_detected <= 1'b0;
            trigger_armed <= 1'b0;
            adc_curr_16 <= 16'd0;
            adc_prev_16 <= 16'd0;
        end
        else begin
            // 只在等待触发状态检测触发
            if (adc_state == STATE_WAIT_TRIGGER && sample_en) begin
                // 将8位ADC数据扩展到16位进行比较
                // 高8位用ADC数据，低8位填充0
                adc_curr_16 <= {adc_a_reg, 8'h00};
                adc_prev_16 <= {adc_a_reg_d1, 8'h00};

                if (trigger_edge == 1'b0) begin // 上升沿触发
                    if (adc_prev_16 < trigger_level && adc_curr_16 >= trigger_level) begin
                        trigger_detected <= 1'b1;
                    end
                    else begin
                        trigger_detected <= 1'b0;
                    end
                end
                else begin // 下降沿触发
                    if (adc_prev_16 > trigger_level && adc_curr_16 <= trigger_level) begin
                        trigger_detected <= 1'b1;
                    end
                    else begin
                        trigger_detected <= 1'b0;
                    end
                end
            end
            else begin
                trigger_detected <= 1'b0;
            end
        end
    end

    //=========================================================================
    // 主状态机（模式选择 + 触发控制）
    //=========================================================================
    reg [31:0] capture_cnt;  // Buffer模式采样点计数

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            adc_state <= STATE_IDLE;
            capture_done <= 1'b0;
            adc_busy <= 1'b0;
            stream_active <= 1'b0;
            sample_counter <= 32'd0;
            capture_cnt <= 32'd0;
            fifo_wr_data <= 8'd0;
            fifo_wr_en <= 1'b0;
        end
        else begin

            //=================================================================
            // 模式选择
            //=================================================================
            case (mode_select)
                //=============================================================
                // 流模式：连续采集，实时传输（V5.0支持触发）
                // 支持触发：启动后等待触发条件，触发后连续采集直到停止
                //=============================================================
                1'b0: begin
                    case (adc_state)
                        STATE_IDLE: begin
                            // 空闲状态
                            adc_busy <= 1'b0;
                            stream_active <= 1'b0;
                            capture_done <= 1'b0;
                            sample_counter <= 32'd0;
                            fifo_wr_data <= 8'd0;
                            fifo_wr_en <= 1'b0;

                            // 收到启动命令
                            if (capture_start) begin
                                // 🔥 流模式也支持触发
                                if (trigger_en) begin
                                    adc_state <= STATE_WAIT_TRIGGER;
                                    adc_busy <= 1'b1;
                                end
                                else begin
                                    adc_state <= STATE_CAPTURING;
                                end
                            end
                        end

                        // 🔥 新增：流模式触发等待状态
                        STATE_WAIT_TRIGGER: begin
                            adc_busy <= 1'b1;
                            stream_active <= 1'b0;
                            fifo_wr_en <= 1'b0;

                            // 检测到触发或手动停止
                            if (trigger_detected) begin
                                adc_state <= STATE_CAPTURING;
                                stream_active <= 1'b1;
                            end
                            else if (capture_stop) begin
                                adc_state <= STATE_IDLE;
                                adc_busy <= 1'b0;
                            end
                        end

                        STATE_CAPTURING: begin
                            // 采集状态
                            adc_busy <= 1'b1;
                            stream_active <= !fifo_full;
                            capture_done <= 1'b0;

                            // 只在收到停止命令时切换到空闲状态
                            if (capture_stop) begin
                                adc_state <= STATE_IDLE;
                                fifo_wr_en <= 1'b0;
                            end
                            else begin
                                // 连续采样并写入FIFO
                                if (sample_en && !fifo_full) begin
                                    fifo_wr_data <= adc_a_reg;
                                    fifo_wr_en <= 1'b1;
                                    sample_counter <= sample_counter + 32'd1;
                                end
                                else begin
                                    fifo_wr_en <= 1'b0;
                                end
                            end
                        end

                        default: begin
                            // 异常状态，返回IDLE
                            adc_state <= STATE_IDLE;
                            adc_busy <= 1'b0;
                            stream_active <= 1'b0;
                        end
                    endcase
                end

                //=============================================================
                // Buffer模式：单次触发采集
                //=============================================================
                1'b1: begin
                    case (adc_state)
                        //=====================================================
                        // IDLE: 等待启动命令
                        //=====================================================
                        STATE_IDLE: begin
                            adc_busy <= 1'b0;
                            stream_active <= 1'b0;
                            capture_done <= 1'b0;
                            sample_counter <= 32'd0;
                            capture_cnt <= 32'd0;
                            fifo_wr_en <= 1'b0;

                            // 收到启动命令
                            if (capture_start) begin
                                // 判断是否需要触发
                                if (trigger_en) begin
                                    adc_state <= STATE_WAIT_TRIGGER;
                                end
                                else begin
                                    adc_state <= STATE_CAPTURING;
                                end
                                adc_busy <= 1'b1;
                            end
                        end

                        //=====================================================
                        // WAIT_TRIGGER: 等待触发条件满足
                        //=====================================================
                        STATE_WAIT_TRIGGER: begin
                            adc_busy <= 1'b1;
                            stream_active <= 1'b0;
                            fifo_wr_en <= 1'b0;

                            // 检测到触发或手动停止
                            if (trigger_detected) begin
                                adc_state <= STATE_CAPTURING;
                                stream_active <= 1'b1;
                            end
                            else if (capture_stop) begin
                                adc_state <= STATE_IDLE;
                                adc_busy <= 1'b0;
                            end
                        end

                        //=====================================================
                        // CAPTURING: 采集中
                        //=====================================================
                        STATE_CAPTURING: begin
                            adc_busy <= 1'b1;
                            stream_active <= !fifo_full;

                            // 采样并写入FIFO
                            if (sample_en && !fifo_full) begin
                                fifo_wr_data <= adc_a_reg;
                                fifo_wr_en <= 1'b1;
                                sample_counter <= sample_counter + 32'd1;
                                capture_cnt <= capture_cnt + 32'd1;

                                // 检查是否达到目标点数
                                if (capture_cnt >= capture_length - 1) begin
                                    adc_state <= STATE_DONE;
                                    capture_done <= 1'b1;
                                    adc_busy <= 1'b0;
                                    stream_active <= 1'b0;
                                end
                            end
                            else begin
                                fifo_wr_en <= 1'b0;
                            end

                            // 手动停止
                            if (capture_stop) begin
                                adc_state <= STATE_DONE;
                                capture_done <= 1'b1;
                                adc_busy <= 1'b0;
                                stream_active <= 1'b0;
                                fifo_wr_en <= 1'b0;
                            end
                        end

                        //=====================================================
                        // DONE: 采集完成，等待复位
                        //=====================================================
                        STATE_DONE: begin
                            adc_busy <= 1'b0;
                            stream_active <= 1'b0;
                            fifo_wr_en <= 1'b0;

                            // 收到新的启动命令，返回IDLE
                            if (capture_start) begin
                                adc_state <= STATE_IDLE;
                                capture_done <= 1'b0;
                            end
                        end

                        default: begin
                            adc_state <= STATE_IDLE;
                        end
                    endcase
                end

                default: begin
                    // 默认流模式
                    adc_state <= STATE_CAPTURING;
                end
            endcase
        end
    end

endmodule
