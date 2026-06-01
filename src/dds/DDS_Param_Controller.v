//=============================================================================
// DDS参数控制器
// 功能：接收指令系统的配置命令，管理双通道DDS参数，支持任意波形
//=============================================================================

module DDS_Param_Controller(
        input Clk,
        input Rst_n,

        // 指令接口
        input [7:0] cmd,              // 命令码
        input [7:0] payload_data,     // Payload数据（逐字节）
        input payload_valid,          // Payload有效标志
        input cmd_done,               // 命令完成标志

        // 通道A参数输出
        output reg [2:0] wave_type_a,    // 波形类型
        output reg [31:0] freq_word_a,   // 频率控制字
        output reg [8:0] phase_a,        // 相位（0-359度）
        output reg [7:0] amplitude_a,    // 幅度（0-255）
        output reg [15:0] duty_cycle_a,  // 占空比（0-65535，16位精度）
        output reg enable_a,             // 通道A使能

        // 通道B参数输出
        output reg [2:0] wave_type_b,    // 波形类型
        output reg [31:0] freq_word_b,   // 频率控制字
        output reg [8:0] phase_b,        // 相位（0-359度）
        output reg [7:0] amplitude_b,    // 幅度（0-255）
        output reg [15:0] duty_cycle_b,  // 占空比（0-65535，16位精度）
        output reg enable_b,             // 通道B使能

        // 任意波形RAM写接口
        output reg arb_wr_en_a,          // 通道A任意波形写使能
        output reg arb_wr_en_b,          // 通道B任意波形写使能
        output reg [7:0] arb_wr_addr,    // 任意波形写地址
        output reg [7:0] arb_wr_data,    // 任意波形写数据

        // 状态反馈
        output reg [7:0] status          // 状态码：0=成功 1=参数错误
    );

    //=========================================================================
    // 命令码定义
    //=========================================================================
    localparam CMD_SET_WAVE_A      = 8'h10;  // 设置通道A波形类型
    localparam CMD_SET_WAVE_B      = 8'h11;  // 设置通道B波形类型
    localparam CMD_SET_FREQ_A      = 8'h12;  // 设置通道A频率
    localparam CMD_SET_FREQ_B      = 8'h13;  // 设置通道B频率
    localparam CMD_SET_PHASE_A     = 8'h14;  // 设置通道A相位
    localparam CMD_SET_PHASE_B     = 8'h15;  // 设置通道B相位
    localparam CMD_SET_AMP_A       = 8'h16;  // 设置通道A幅度
    localparam CMD_SET_AMP_B       = 8'h17;  // 设置通道B幅度
    localparam CMD_SET_ENABLE      = 8'h18;  // 设置通道使能
    localparam CMD_SET_ALL_A       = 8'h19;  // 一次性设置通道A所有参数
    localparam CMD_SET_ALL_B       = 8'h1A;  // 一次性设置通道B所有参数
    localparam CMD_GET_STATUS      = 8'h1B;  // 获取当前状态
    localparam CMD_SET_DUTY_A      = 8'h1C;  // 设置通道A占空比
    localparam CMD_SET_DUTY_B      = 8'h1D;  // 设置通道B占空比
    localparam CMD_WRITE_ARB_A     = 8'h1E;  // 写入通道A任意波形数据（256字节）
    localparam CMD_WRITE_ARB_B     = 8'h1F;  // 写入通道B任意波形数据（256字节）

    //=========================================================================
    // 内部寄存器
    //=========================================================================
    reg [7:0] payload_buffer[15:0];  // Payload缓冲区
    reg [3:0] payload_count;         // 已接收的Payload字节数
    reg [7:0] current_cmd;           // 当前处理的命令
    reg [7:0] arb_write_count;       // 任意波形写入计数器
    reg cmd_done_d;                  // cmd_done延迟一拍

    //=========================================================================
    // Payload接收状态机和参数复位
    //=========================================================================
    always @(posedge Clk or negedge Rst_n) begin
        if (!Rst_n) begin
            // 复位所有寄存器到默认值
            payload_count <= 4'd0;
            current_cmd <= 8'd0;
            status <= 8'd0;
            arb_write_count <= 8'd0;
            arb_wr_en_a <= 1'b0;
            arb_wr_en_b <= 1'b0;
            arb_wr_addr <= 8'd0;
            arb_wr_data <= 8'd0;
            cmd_done_d <= 1'b0;

            // 通道A默认：1kHz正弦波，0度相位，满幅度
            wave_type_a <= 3'd0;         // 正弦波
            freq_word_a <= 32'd34360;    // 1kHz @ 125MHz
            phase_a <= 9'd0;             // 0度
            amplitude_a <= 8'd255;       // 满幅度
            duty_cycle_a <= 16'd32768;   // 50%占空比（16位精度）
            enable_a <= 1'b1;            // 使能

            // 通道B默认：1kHz正弦波，0度相位，满幅度
            wave_type_b <= 3'd0;         // 正弦波
            freq_word_b <= 32'd34360;    // 1kHz @ 125MHz
            phase_b <= 9'd0;             // 0度相位（改为与A通道相同）
            amplitude_b <= 8'd255;       // 满幅度
            duty_cycle_b <= 16'd32768;   // 50%占空比（16位精度）
            enable_b <= 1'b1;            // 使能
        end
        else begin
            // 默认关闭写使能
            arb_wr_en_a <= 1'b0;
            arb_wr_en_b <= 1'b0;

            // cmd_done延迟一拍
            cmd_done_d <= cmd_done;

            // 🔧 修复：直接使用cmd输入（在payload接收期间cmd就已经有效）
            // 这样任意波形命令第一次发送时就能正确写入
            // 任意波形数据写入处理（实时写入，每收到一字节立即写RAM）
            if (payload_valid && (cmd == CMD_WRITE_ARB_A || cmd == CMD_WRITE_ARB_B)) begin
                arb_wr_addr <= arb_write_count;
                arb_wr_data <= payload_data;

                if (cmd == CMD_WRITE_ARB_A) begin
                    arb_wr_en_a <= 1'b1;
                    // 🆕 自动切换到任意波形模式
                    // wave_type_a <= 3'd6;  // 先不自动切换，让上位机控制
                end
                else begin
                    arb_wr_en_b <= 1'b1;
                    // 🆕 自动切换到任意波形模式
                    // wave_type_b <= 3'd6;  // 先不自动切换，让上位机控制
                end

                arb_write_count <= arb_write_count + 1;
            end
            // 命令完成，处理参数（优先级最高，防止payload_count竞态）
            if (cmd_done) begin
                // 保存命令码用于后续判断
                current_cmd <= cmd;

                case (cmd)
                    //-----------------------------------------------------
                    // 设置通道A波形类型
                    // Payload: [wave_type] (1字节)
                    //   0=正弦 1=方波 2=三角 3=锯齿 4=反锯齿 5=脉冲 6=任意波形
                    //-----------------------------------------------------
                    CMD_SET_WAVE_A: begin
                        if (payload_buffer[0] <= 3'd6) begin
                            wave_type_a <= payload_buffer[0][2:0];
                            status <= 8'd0;  // 成功
                        end
                        else begin
                            status <= 8'd1;  // 参数错误
                        end
                    end

                    //-----------------------------------------------------
                    // 设置通道B波形类型
                    //-----------------------------------------------------
                    CMD_SET_WAVE_B: begin
                        if (payload_buffer[0] <= 3'd6) begin
                            wave_type_b <= payload_buffer[0][2:0];
                            status <= 8'd0;
                        end
                        else begin
                            status <= 8'd1;
                        end
                    end

                    //-----------------------------------------------------
                    // 设置通道A频率
                    // Payload: [freq_word_3] [freq_word_2] [freq_word_1] [freq_word_0] (4字节, 大端序)
                    //   上位机已经计算好频率控制字
                    //   接收顺序：高字节在前，低字节在后
                    //-----------------------------------------------------
                    CMD_SET_FREQ_A: begin
                        // 大端序接收：payload_buffer[0]是最高字节
                        freq_word_a <= {payload_buffer[0], payload_buffer[1],
                                        payload_buffer[2], payload_buffer[3]};
                        status <= 8'd0;
                    end

                    //-----------------------------------------------------
                    // 设置通道B频率
                    //-----------------------------------------------------
                    CMD_SET_FREQ_B: begin
                        // 大端序接收：payload_buffer[0]是最高字节
                        freq_word_b <= {payload_buffer[0], payload_buffer[1],
                                        payload_buffer[2], payload_buffer[3]};
                        status <= 8'd0;
                    end

                    //-----------------------------------------------------
                    // 设置通道A相位
                    // Payload: [phase_h] [phase_l] (2字节, 大端序)
                    //   相位范围：0-359度
                    //-----------------------------------------------------
                    CMD_SET_PHASE_A: begin
                        // 大端序：payload_buffer[0]是高字节，payload_buffer[1]是低字节
                        phase_a <= {payload_buffer[0], payload_buffer[1]};
                        if ({payload_buffer[0], payload_buffer[1]} < 360)
                            status <= 8'd0;
                        else
                            status <= 8'd1;  // 超出范围
                    end

                    //-----------------------------------------------------
                    // 设置通道B相位
                    //-----------------------------------------------------
                    CMD_SET_PHASE_B: begin
                        // 大端序：payload_buffer[0]是高字节，payload_buffer[1]是低字节
                        phase_b <= {payload_buffer[0], payload_buffer[1]};
                        if ({payload_buffer[0], payload_buffer[1]} < 360)
                            status <= 8'd0;
                        else
                            status <= 8'd1;
                    end

                    //-----------------------------------------------------
                    // 设置通道A幅度
                    // Payload: [amplitude] (1字节, 0-255)
                    //-----------------------------------------------------
                    CMD_SET_AMP_A: begin
                        amplitude_a <= payload_buffer[0];
                        status <= 8'd0;
                    end

                    //-----------------------------------------------------
                    // 设置通道B幅度
                    //-----------------------------------------------------
                    CMD_SET_AMP_B: begin
                        amplitude_b <= payload_buffer[0];
                        status <= 8'd0;
                    end

                    //-----------------------------------------------------
                    // 设置通道使能
                    // Payload: [enable_flags] (1字节)
                    //   bit[0] = 通道A使能
                    //   bit[1] = 通道B使能
                    //-----------------------------------------------------
                    CMD_SET_ENABLE: begin
                        enable_a <= payload_buffer[0][0];
                        enable_b <= payload_buffer[0][1];
                        status <= 8'd0;
                    end

                    //-----------------------------------------------------
                    // 设置通道A占空比（16位精度升级版）
                    // Payload: [duty_h] [duty_l] (2字节, 大端序, 0-65535)
                    //-----------------------------------------------------
                    CMD_SET_DUTY_A: begin
                        // 大端序：payload_buffer[0]是高字节，payload_buffer[1]是低字节
                        duty_cycle_a <= {payload_buffer[0], payload_buffer[1]};
                        status <= 8'd0;
                    end

                    //-----------------------------------------------------
                    // 设置通道B占空比（16位精度升级版）
                    //-----------------------------------------------------
                    CMD_SET_DUTY_B: begin
                        // 大端序：payload_buffer[0]是高字节，payload_buffer[1]是低字节
                        duty_cycle_b <= {payload_buffer[0], payload_buffer[1]};
                        status <= 8'd0;
                    end

                    //-----------------------------------------------------
                    // 一次性设置通道A所有参数（16位占空比升级版）
                    // Payload: [wave_type] [freq_3] [freq_2] [freq_1] [freq_0]
                    //          [phase_h] [phase_l] [amplitude] [reserved] [duty_h] [duty_l] (11字节, 大端序)
                    // 说明：所有多字节数据都是大端序（高字节在前）
                    //-----------------------------------------------------
                    CMD_SET_ALL_A: begin
                        wave_type_a <= payload_buffer[0][2:0];
                        // 频率控制字：大端序 [1][2][3][4] → 高到低
                        freq_word_a <= {payload_buffer[1], payload_buffer[2],
                                        payload_buffer[3], payload_buffer[4]};
                        // 相位：大端序 [5][6] → 高到低
                        phase_a <= {payload_buffer[5], payload_buffer[6]};
                        amplitude_a <= payload_buffer[7];
                        // payload_buffer[8] 保留字节（原偏置位置）
                        // 16位占空比：大端序 [9][10] → 高到低
                        duty_cycle_a <= {payload_buffer[9], payload_buffer[10]};
                        status <= 8'd0;
                    end

                    //-----------------------------------------------------
                    // 一次性设置通道B所有参数（16位占空比升级版）
                    //-----------------------------------------------------
                    CMD_SET_ALL_B: begin
                        wave_type_b <= payload_buffer[0][2:0];
                        // 频率控制字：大端序 [1][2][3][4] → 高到低
                        freq_word_b <= {payload_buffer[1], payload_buffer[2],
                                        payload_buffer[3], payload_buffer[4]};
                        // 相位：大端序 [5][6] → 高到低
                        phase_b <= {payload_buffer[5], payload_buffer[6]};
                        amplitude_b <= payload_buffer[7];
                        // payload_buffer[8] 保留字节（原偏置位置）
                        // 16位占空比：大端序 [9][10] → 高到低
                        duty_cycle_b <= {payload_buffer[9], payload_buffer[10]};
                        status <= 8'd0;
                    end

                    //-----------------------------------------------------
                    // 写入通道A任意波形数据
                    // Payload: [data_0] [data_1] ... [data_255] (256字节)
                    // 说明：逐字节写入RAM，每字节代表一个采样点的幅值（0-255）
                    //-----------------------------------------------------
                    CMD_WRITE_ARB_A: begin
                        // 所有数据已经在payload_valid时实时写入RAM
                        arb_write_count <= 8'd0;  // 重置计数器
                        status <= 8'd0;
                    end

                    //-----------------------------------------------------
                    // 写入通道B任意波形数据
                    //-----------------------------------------------------
                    CMD_WRITE_ARB_B: begin
                        // 所有数据已经在payload_valid时实时写入RAM
                        arb_write_count <= 8'd0;  // 重置计数器
                        status <= 8'd0;
                    end

                    default: begin
                        status <= 8'd2;  // 未知命令
                    end
                endcase

                // 重置Payload计数器
                payload_count <= 4'd0;
            end
            // 接收Payload数据（只在没有cmd_done时处理，避免竞态）
            // 对于任意波形命令，不使用payload_buffer，直接实时写入
            else if (payload_valid && cmd != CMD_WRITE_ARB_A && cmd != CMD_WRITE_ARB_B) begin
                payload_buffer[payload_count] <= payload_data;
                payload_count <= payload_count + 1;
            end
        end
    end

    //=========================================================================
    // 频率控制字计算函数
    // 输入：频率（Hz）
    // 输出：32位频率控制字
    // 公式：Fword = (Freq_Hz * 2^32) / Fclk
    //       其中 Fclk = 125MHz
    //
    // 精确计算：
    // Fword = (Freq_Hz * 4294967296) / 125000000
    //       = (Freq_Hz * 4294967296) / 125000000
    //       = Freq_Hz * 34.359738368
    //
    // 使用定点数计算：
    // 为了保持精度，我们使用：Fword = (Freq_Hz * 34360) （误差<0.01%）
    // 但这里我们不能直接接收频率值，应该直接使用上位机发送的频率控制字
    //=========================================================================
    function [31:0] calc_freq_word;
        input [31:0] freq_hz;
        reg [63:0] temp;
        begin
            // 注意：这里的输入实际上已经是频率（Hz），需要转换为控制字
            // 由于Verilog综合工具对大数乘法支持有限，这里直接返回输入值
            // 让上位机负责计算频率控制字

            // 如果上位机发送的是频率值（Hz），这里进行转换
            // 但由于大端序转换可能有问题，我们直接使用输入值
            calc_freq_word = freq_hz;
        end
    endfunction

endmodule
