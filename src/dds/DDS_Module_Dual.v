/***************************************************
*	Module Name		:	DDS_Module_Dual		   
*	Engineer		:	Enhanced Version
*	Description		:  双通道DDS模块，支持：
*                      - 7种波形（正弦、方波、三角、锯齿、反锯齿、脉冲、任意波形）
*                      - 频率无级调节（1Hz步进，1Hz-10MHz范围）
*                      - 独立相位控制（0-359度，1度步进）
*                      - 独立幅度控制（0-255）
*                      - 脉冲波形占空比可调（1%-99%）
*                      - 任意波形：256点自定义波表
*                      - 通过指令系统控制所有参数
**************************************************/

module DDS_Module_Dual(
        input Clk,               // 系统时钟（125MHz）
        input Rst_n,             // 系统复位
        input EN,                // DDS模块使能

        // 通道A配置
        input [2:0] wave_type_a, // 波形类型：0=正弦 1=方波 2=三角 3=锯齿 4=反锯齿 5=脉冲 6=任意波形
        input [31:0] freq_word_a,// 频率控制字
        input [8:0] phase_a,     // 相位偏移（0-359度）
        input [7:0] amplitude_a, // 幅度控制（0-255）
        input [15:0] duty_cycle_a,// 占空比（0-65535，对应0-100%，精度0.0015%）

        // 通道B配置
        input [2:0] wave_type_b, // 波形类型
        input [31:0] freq_word_b,// 频率控制字
        input [8:0] phase_b,     // 相位偏移（0-359度）
        input [7:0] amplitude_b, // 幅度控制（0-255）
        input [15:0] duty_cycle_b,// 占空比（0-65535，对应0-100%，精度0.0015%）

        // 任意波形RAM接口
        output [7:0] arb_rd_addr_a,  // 通道A任意波形读地址
        input [7:0] arb_rd_data_a,   // 通道A任意波形读数据
        output [7:0] arb_rd_addr_b,  // 通道B任意波形读地址
        input [7:0] arb_rd_data_b,   // 通道B任意波形读数据

        // DAC输出
        output DA_Clk,           // DA数据输出时钟
        output reg [7:0] DA0_Data, // 通道A输出
        output reg [7:0] DA1_Data  // 通道B输出
    );

    //=========================================================================
    // 通道A信号
    //=========================================================================
    wire [7:0] wave_sin_a, wave_square_a, wave_triangle_a;
    wire [7:0] wave_sawtooth_a, wave_inv_sawtooth_a;
    reg [7:0] wave_pulse_a;      // 脉冲波形（实时计算）
    reg [31:0] phase_acc_a;      // 相位累加器
    reg [31:0] phase_offset_a;   // 相位偏移量（度转相位累加器值）
    reg [31:0] duty_threshold_a; // 占空比阈值（相位累加器比较值）
    reg [7:0] rom_addr_a;        // ROM地址
    reg [7:0] wave_raw_a;        // 原始波形数据
    reg [15:0] wave_scaled_a;    // 幅度调节后（16位中间值）

    //=========================================================================
    // 通道B信号
    //=========================================================================
    wire [7:0] wave_sin_b, wave_square_b, wave_triangle_b;
    wire [7:0] wave_sawtooth_b, wave_inv_sawtooth_b;
    reg [7:0] wave_pulse_b;      // 脉冲波形（实时计算）
    reg [31:0] phase_acc_b;      // 相位累加器
    reg [31:0] phase_offset_b;   // 相位偏移量
    reg [31:0] duty_threshold_b; // 占空比阈值
    reg [7:0] rom_addr_b;        // ROM地址
    reg [7:0] wave_raw_b;        // 原始波形数据
    reg [15:0] wave_scaled_b;    // 幅度调节后

    //=========================================================================
    // 相位偏移量计算 & 占空比阈值计算（16位精度优化版）
    // 将角度（0-359）转换为32位相位累加器值
    // phase_offset = (degree * 2^32) / 360
    // 简化：phase_offset = (degree * 11930465) >> 12  (近似计算)
    //
    // 占空比阈值计算（16位精度，参考PWM模块实现）：
    // duty_threshold = (duty_cycle * 2^32) / 65536
    // 简化：duty_threshold = duty_cycle << 16  (左移16位)
    // 精度：0.0015% (1/65536)
    //=========================================================================
    always @(posedge Clk or negedge Rst_n) begin
        if (!Rst_n) begin
            phase_offset_a <= 32'd0;
            phase_offset_b <= 32'd0;
            duty_threshold_a <= 32'd2147483648;  // 默认50% (32768 << 16)
            duty_threshold_b <= 32'd2147483648;  // 默认50% (32768 << 16)
        end
        else begin
            // 角度转相位偏移：phase = (degree * 2^32) / 360
            // 使用查找表或简化计算：每度约 11930465 (2^32/360)
            phase_offset_a <= (phase_a * 32'd11930465);
            phase_offset_b <= (phase_b * 32'd11930465);

            // 16位占空比转32位阈值（参考PWM模块）：
            // threshold = duty_cycle << 16
            // 范围：0x00000000 (0%) 到 0xFFFF0000 (100%)
            // 精度：1/65536 = 0.0015%
            duty_threshold_a <= {duty_cycle_a, 16'd0};  // 左移16位
            duty_threshold_b <= {duty_cycle_b, 16'd0};  // 左移16位
        end
    end

    //=========================================================================
    // 通道A - 相位累加器
    //=========================================================================
    always @(posedge Clk or negedge Rst_n) begin
        if (!Rst_n)
            phase_acc_a <= 32'd0;
        else if (!EN)
            phase_acc_a <= 32'd0;
        else
            phase_acc_a <= phase_acc_a + freq_word_a;
    end

    //=========================================================================
    // 通道B - 相位累加器
    //=========================================================================
    always @(posedge Clk or negedge Rst_n) begin
        if (!Rst_n)
            phase_acc_b <= 32'd0;
        else if (!EN)
            phase_acc_b <= 32'd0;
        else
            phase_acc_b <= phase_acc_b + freq_word_b;
    end

    //=========================================================================
    // 生成ROM查找地址（加上相位偏移）
    //=========================================================================
    wire [31:0] phase_total_a = phase_acc_a + phase_offset_a;
    wire [31:0] phase_total_b = phase_acc_b + phase_offset_b;

    always @(posedge Clk or negedge Rst_n) begin
        if (!Rst_n) begin
            rom_addr_a <= 8'd0;
            rom_addr_b <= 8'd0;
        end
        else if (!EN) begin
            rom_addr_a <= 8'd0;
            rom_addr_b <= 8'd0;
        end
        else begin
            rom_addr_a <= phase_total_a[31:24];  // 取高8位作为地址
            rom_addr_b <= phase_total_b[31:24];
        end
    end

    //=========================================================================
    // 任意波形RAM地址输出（与ROM地址相同）
    //=========================================================================
    assign arb_rd_addr_a = rom_addr_a;
    assign arb_rd_addr_b = rom_addr_b;

    //=========================================================================
    // 例化波形ROM - 通道A
    //=========================================================================
    sin_rom_a8d8 rom_sin_a(
                     .addr(rom_addr_a),
                     .clk(Clk),
                     .q(wave_sin_a)
                 );

    square_wave_rom_a8d8 rom_square_a(
                             .addr(rom_addr_a),
                             .clk(Clk),
                             .q(wave_square_a)
                         );

    triangular_rom_a8d8 rom_triangle_a(
                            .addr(rom_addr_a),
                            .clk(Clk),
                            .q(wave_triangle_a)
                        );

    sawtooth_rom_a8d8 rom_sawtooth_a(
                          .addr(rom_addr_a),
                          .clk(Clk),
                          .q(wave_sawtooth_a)
                      );

    inv_sawtooth_rom_a8d8 rom_inv_sawtooth_a(
                              .addr(rom_addr_a),
                              .clk(Clk),
                              .q(wave_inv_sawtooth_a)
                          );

    //=========================================================================
    // 例化波形ROM - 通道B
    //=========================================================================
    sin_rom_a8d8 rom_sin_b(
                     .addr(rom_addr_b),
                     .clk(Clk),
                     .q(wave_sin_b)
                 );

    square_wave_rom_a8d8 rom_square_b(
                             .addr(rom_addr_b),
                             .clk(Clk),
                             .q(wave_square_b)
                         );

    triangular_rom_a8d8 rom_triangle_b(
                            .addr(rom_addr_b),
                            .clk(Clk),
                            .q(wave_triangle_b)
                        );

    sawtooth_rom_a8d8 rom_sawtooth_b(
                          .addr(rom_addr_b),
                          .clk(Clk),
                          .q(wave_sawtooth_b)
                      );

    inv_sawtooth_rom_a8d8 rom_inv_sawtooth_b(
                              .addr(rom_addr_b),
                              .clk(Clk),
                              .q(wave_inv_sawtooth_b)
                          );

    //=========================================================================
    // 脉冲波形生成 - 通道A（16位精度优化版）
    // 根据相位累加器和占空比阈值实时生成脉冲
    // 占空比定义：高电平时间占整个周期的百分比
    // 16位精度：0-65535 对应 0-100%，精度 0.0015%
    //=========================================================================
    always @(posedge Clk or negedge Rst_n) begin
        if (!Rst_n)
            wave_pulse_a <= 8'd0;
        else if (!EN)
            wave_pulse_a <= 8'd0;
        else begin
            // 16位精度占空比比较：
            // duty_threshold = duty_cycle << 16
            // 当 phase_total < duty_threshold 时输出高电平
            // 例：50% = 32768，threshold = 0x80000000
            //     25% = 16384，threshold = 0x40000000
            if (phase_total_a < duty_threshold_a)
                wave_pulse_a <= 8'd255;  // 高电平（前duty_cycle%的时间）
            else
                wave_pulse_a <= 8'd0;    // 低电平（后(100-duty_cycle)%的时间）
        end
    end

    //=========================================================================
    // 脉冲波形生成 - 通道B（16位精度优化版）
    //=========================================================================
    always @(posedge Clk or negedge Rst_n) begin
        if (!Rst_n)
            wave_pulse_b <= 8'd0;
        else if (!EN)
            wave_pulse_b <= 8'd0;
        else begin
            // 16位精度占空比比较
            if (phase_total_b < duty_threshold_b)
                wave_pulse_b <= 8'd255;  // 高电平（前duty_cycle%的时间）
            else
                wave_pulse_b <= 8'd0;    // 低电平（后(100-duty_cycle)%的时间）
        end
    end

    //=========================================================================
    // 通道A - 波形选择（包括脉冲波形和任意波形）
    //=========================================================================
    always @(*) begin
        case (wave_type_a)
            3'd0:
                wave_raw_a = wave_sin_a;
            3'd1:
                wave_raw_a = wave_square_a;
            3'd2:
                wave_raw_a = wave_triangle_a;
            3'd3:
                wave_raw_a = wave_sawtooth_a;
            3'd4:
                wave_raw_a = wave_inv_sawtooth_a;
            3'd5:
                wave_raw_a = wave_pulse_a;     // 脉冲波形
            3'd6:
                wave_raw_a = arb_rd_data_a;    // 任意波形
            default:
                wave_raw_a = wave_sin_a;
        endcase
    end

    //=========================================================================
    // 通道B - 波形选择（包括脉冲波形和任意波形）
    //=========================================================================
    always @(*) begin
        case (wave_type_b)
            3'd0:
                wave_raw_b = wave_sin_b;
            3'd1:
                wave_raw_b = wave_square_b;
            3'd2:
                wave_raw_b = wave_triangle_b;
            3'd3:
                wave_raw_b = wave_sawtooth_b;
            3'd4:
                wave_raw_b = wave_inv_sawtooth_b;
            3'd5:
                wave_raw_b = wave_pulse_b;     // 脉冲波形
            3'd6:
                wave_raw_b = arb_rd_data_b;    // 任意波形
            default:
                wave_raw_b = wave_sin_b;
        endcase
    end

    //=========================================================================
    // 幅度调节 - 通道A
    // 使用乘法器实现：output = (wave * amplitude) / 255
    //=========================================================================
    always @(posedge Clk or negedge Rst_n) begin
        if (!Rst_n) begin
            wave_scaled_a <= 16'd0;
            DA0_Data <= 8'd0;
        end
        else begin
            wave_scaled_a <= wave_raw_a * amplitude_a;
            DA0_Data <= wave_scaled_a[15:8];  // 取高8位作为输出
        end
    end

    //=========================================================================
    // 幅度调节 - 通道B
    //=========================================================================
    always @(posedge Clk or negedge Rst_n) begin
        if (!Rst_n) begin
            wave_scaled_b <= 16'd0;
            DA1_Data <= 8'd0;
        end
        else begin
            wave_scaled_b <= wave_raw_b * amplitude_b;
            DA1_Data <= wave_scaled_b[15:8];  // 取高8位作为输出
        end
    end

    //=========================================================================
    // 输出DA时钟
    //=========================================================================
    assign DA_Clk = Clk;

endmodule
