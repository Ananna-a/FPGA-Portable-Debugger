//=============================================================================
// 8路PWM参数控制器 (基于DDS相位累加器原理)
// 功能：
//   1. 解析CDC命令并配置PWM参数
//   2. 支持8路独立频率和占空比控制
//   3. 频率范围: 1Hz - 1MHz
//   4. 占空比精度: 0-100% (16位精度, 0.0015%步进)
// 设计思路：
//   - 使用DDS频率字控制频率（参考DDS_Module_Dual）
//   - 频率字由上位机精确计算：freq_word = (freq_hz * 2^32) / 50MHz
//   - 避免FPGA内部近似计算导致的精度损失
// 命令码：
//   0x50: PWM配置 - payload: [通道ID][频率字(32bit)][占空比(16bit)]
//   0x51: PWM使能 - payload: [使能掩码]
//   0x52: PWM停止 - payload: 无
//=============================================================================

module pwm_param_controller(
        input clk,              // 系统时钟 (50MHz)
        input rst_n,

        // CDC命令接口
        input [7:0] cmd,
        input [7:0] payload_data,
        input payload_valid,
        input cmd_done,

        // PWM输出
        output [7:0] pwm_output,
        output reg pwm_enable,

        // 状态输出
        output [7:0] status,

        // 新增：配置更新信号（同步到各PWM生成器）
        output reg config_update
    );

    //=========================================================================
    // 命令码定义
    //=========================================================================
    localparam CMD_PWM_CONFIG  = 8'h50;  // PWM配置
    localparam CMD_PWM_ENABLE  = 8'h51;  // PWM使能控制
    localparam CMD_PWM_STOP    = 8'h52;  // PWM停止

    //=========================================================================
    // PWM配置寄存器 - 8通道
    //=========================================================================
    reg [31:0] pwm_freq_word [0:7]; // 每通道DDS频率字 - 32位
    reg [15:0] pwm_duty [0:7];      // 每通道占空比(0-65535) - 16位
    reg [7:0] channel_enable_mask;  // 通道使能掩码

    // 初始化
    integer i;
    initial begin
        for (i = 0; i < 8; i = i + 1) begin
            pwm_freq_word[i] = 32'd85899;  // 默认1kHz的频率字
            pwm_duty[i] = 16'd32768;       // 默认50%
        end
        channel_enable_mask = 8'h0;
    end

    //=========================================================================
    // Payload接收状态机
    //=========================================================================
    localparam S_IDLE = 2'd0;
    localparam S_RECV_CONFIG = 2'd1;

    reg [1:0] state;
    reg cmd_latched;
    reg [3:0] payload_counter;

    // 配置命令临时缓冲
    reg [2:0] config_channel_id;
    reg [31:0] config_freq_word;    // 接收到的频率字（上位机已计算好）
    reg [15:0] config_duty;

    //=========================================================================
    // 命令解析与参数接收
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= S_IDLE;
            cmd_latched <= 1'b0;
            payload_counter <= 4'h0;
            channel_enable_mask <= 8'h0;
            pwm_enable <= 1'b0;
            config_channel_id <= 3'h0;
            config_freq_word <= 32'd85899;  // 默认1kHz的频率字
            config_duty <= 16'd32768;
            config_update <= 1'b0;
        end
        else begin
            // ========== 状态4'd7：参数写入周期（打一拍稳定数据）==========
            // 这个状态不依赖payload_valid，在接收完所有数据后自动执行
            // ⚠️ 优先级最高：确保参数写入不被其他逻辑打断
            if (state == S_RECV_CONFIG && payload_counter == 4'd7) begin
                // ⏱️ 数据稳定周期：config_freq_word和config_duty已经稳定
                pwm_freq_word[config_channel_id] <= config_freq_word;
                pwm_duty[config_channel_id] <= config_duty;

                // ✅ 触发配置更新（仅持续一个时钟周期）
                config_update <= 1'b1;

                payload_counter <= 4'd0;
                state <= S_IDLE;
                cmd_latched <= 1'b0;  // ✅ 清除锁存，允许接收新命令
            end
            // cmd_done信号处理
            else if (cmd_done) begin
                cmd_latched <= 1'b0;
                payload_counter <= 4'h0;

                case (cmd)
                    CMD_PWM_STOP: begin
                        pwm_enable <= 1'b0;
                        // ⚠️ 不清空channel_enable_mask，保持配置
                    end
                    CMD_PWM_ENABLE: begin
                        pwm_enable <= (channel_enable_mask != 8'h0);
                    end
                endcase

                state <= S_IDLE;
            end
            // ========== PWM配置 (0x50) ==========
            // payload: [通道ID][频率字H3][频率字H2][频率字H1][频率字L][占空比H][占空比L]
            // ⚠️ 频率字由上位机精确计算：freq_word = (freq_hz * 2^32) / 50MHz
            // ⚠️ 只有在IDLE状态且没有锁存时才接受新命令
            else if (payload_valid && cmd == CMD_PWM_CONFIG && state == S_IDLE && !cmd_latched) begin
                // 第一个字节：通道ID
                config_channel_id <= payload_data[2:0];
                payload_counter <= 4'd1;
                cmd_latched <= 1'b1;
                state <= S_RECV_CONFIG;
            end
            // 在S_RECV_CONFIG状态下继续接收payload
            else if (payload_valid && cmd == CMD_PWM_CONFIG && state == S_RECV_CONFIG && cmd_latched) begin
                case (payload_counter)
                    4'd1: begin
                        // 第2字节：频率字最高字节
                        config_freq_word[31:24] <= payload_data;
                        payload_counter <= 4'd2;
                    end
                    4'd2: begin
                        // 第3字节：频率字
                        config_freq_word[23:16] <= payload_data;
                        payload_counter <= 4'd3;
                    end
                    4'd3: begin
                        // 第4字节：频率字
                        config_freq_word[15:8] <= payload_data;
                        payload_counter <= 4'd4;
                    end
                    4'd4: begin
                        // 第5字节：频率字最低字节
                        config_freq_word[7:0] <= payload_data;
                        payload_counter <= 4'd5;
                    end
                    4'd5: begin
                        // 第6字节：占空比高字节
                        config_duty[15:8] <= payload_data;
                        payload_counter <= 4'd6;
                    end
                    4'd6: begin
                        // 第7字节：占空比低字节（最后一个字节）
                        config_duty[7:0] <= payload_data;

                        // ✅ 进入等待状态，下个周期会由独立逻辑处理写入
                        payload_counter <= 4'd7;
                    end
                    default: begin
                        payload_counter <= 4'd0;
                        state <= S_IDLE;
                    end
                endcase
            end

            // ========== PWM使能控制 (0x51) ==========
            // payload: [使能掩码]
            else if (payload_valid && cmd == CMD_PWM_ENABLE && !cmd_latched) begin
                channel_enable_mask <= payload_data;
                cmd_latched <= 1'b1;
            end
        end
    end

    //=========================================================================
    // PWM生成引擎 - 8通道
    //=========================================================================
    wire pwm_out_ch0, pwm_out_ch1, pwm_out_ch2, pwm_out_ch3;
    wire pwm_out_ch4, pwm_out_ch5, pwm_out_ch6,pwm_out_ch7;

    // 实例化8个PWM生成器
    pwm_generator pwm_gen_ch0(
                      .clk        (clk),
                      .rst_n      (rst_n),
                      .enable     (pwm_enable & channel_enable_mask[0]),
                      .freq_word  (pwm_freq_word[0]),
                      .duty_cycle (pwm_duty[0]),
                      .config_update(config_update),  // 关键！
                      .pwm_out    (pwm_out_ch0)
                  );

    pwm_generator pwm_gen_ch1(
                      .clk        (clk),
                      .rst_n      (rst_n),
                      .enable     (pwm_enable & channel_enable_mask[1]),
                      .freq_word  (pwm_freq_word[1]),
                      .duty_cycle (pwm_duty[1]),
                      .config_update(config_update),
                      .pwm_out    (pwm_out_ch1)
                  );

    pwm_generator pwm_gen_ch2(
                      .clk        (clk),
                      .rst_n      (rst_n),
                      .enable     (pwm_enable & channel_enable_mask[2]),
                      .freq_word  (pwm_freq_word[2]),
                      .duty_cycle (pwm_duty[2]),
                      .config_update(config_update),
                      .pwm_out    (pwm_out_ch2)
                  );

    pwm_generator pwm_gen_ch3(
                      .clk        (clk),
                      .rst_n      (rst_n),
                      .enable     (pwm_enable & channel_enable_mask[3]),
                      .freq_word  (pwm_freq_word[3]),
                      .duty_cycle (pwm_duty[3]),
                      .config_update(config_update),
                      .pwm_out    (pwm_out_ch3)
                  );

    pwm_generator pwm_gen_ch4(
                      .clk        (clk),
                      .rst_n      (rst_n),
                      .enable     (pwm_enable & channel_enable_mask[4]),
                      .freq_word  (pwm_freq_word[4]),
                      .duty_cycle (pwm_duty[4]),
                      .config_update(config_update),
                      .pwm_out    (pwm_out_ch4)
                  );

    pwm_generator pwm_gen_ch5(
                      .clk        (clk),
                      .rst_n      (rst_n),
                      .enable     (pwm_enable & channel_enable_mask[5]),
                      .freq_word  (pwm_freq_word[5]),
                      .duty_cycle (pwm_duty[5]),
                      .config_update(config_update),
                      .pwm_out    (pwm_out_ch5)
                  );

    pwm_generator pwm_gen_ch6(
                      .clk        (clk),
                      .rst_n      (rst_n),
                      .enable     (pwm_enable & channel_enable_mask[6]),
                      .freq_word  (pwm_freq_word[6]),
                      .duty_cycle (pwm_duty[6]),
                      .config_update(config_update),
                      .pwm_out    (pwm_out_ch6)
                  );

    pwm_generator pwm_gen_ch7(
                      .clk        (clk),
                      .rst_n      (rst_n),
                      .enable     (pwm_enable & channel_enable_mask[7]),
                      .freq_word  (pwm_freq_word[7]),
                      .duty_cycle (pwm_duty[7]),
                      .config_update(config_update),
                      .pwm_out    (pwm_out_ch7)
                  );

    //=========================================================================
    // 输出组合
    //=========================================================================
    assign pwm_output = {
               pwm_out_ch7,
               pwm_out_ch6,
               pwm_out_ch5,
               pwm_out_ch4,
               pwm_out_ch3,
               pwm_out_ch2,
               pwm_out_ch1,
               pwm_out_ch0
           };

    // 状态输出
    assign status = {
               pwm_enable,           // bit[7]: 全局使能
               3'b0,                 // bit[6:4]: 预留
               config_channel_id,    // bit[3:1]: 当前配置通道
               (state == S_RECV_CONFIG)  // bit[0]: 接收中
           };

endmodule
