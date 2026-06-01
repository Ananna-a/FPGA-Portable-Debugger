//=============================================================================
// 通用指令控制模块
// 功能：提供16个32位通用寄存器，用户可根据需要自定义用途
// 设计理念：完全通用，不预设任何特定功能
//=============================================================================

module cmd_ctrl(
        input Clk,
        input Reset_n,
        input [31:0] cmd_data,      // 指令数据（32位）
        input [7:0] cmd_addr,       // 指令地址（8位）
        input cmdvalid,             // 指令有效标志

        // 16个通用32位寄存器（用户自定义用途）
        output reg [31:0] REG0,     // 地址0x00 - 通用寄存器0（建议：控制命令）
        output reg [31:0] REG1,     // 地址0x01 - 通用寄存器1
        output reg [31:0] REG2,     // 地址0x02 - 通用寄存器2
        output reg [31:0] REG3,     // 地址0x03 - 通用寄存器3
        output reg [31:0] REG4,     // 地址0x04 - 通用寄存器4
        output reg [31:0] REG5,     // 地址0x05 - 通用寄存器5
        output reg [31:0] REG6,     // 地址0x06 - 通用寄存器6
        output reg [31:0] REG7,     // 地址0x07 - 通用寄存器7
        output reg [31:0] REG8,     // 地址0x08 - 通用寄存器8
        output reg [31:0] REG9,     // 地址0x09 - 通用寄存器9
        output reg [31:0] REG10,    // 地址0x0A - 通用寄存器10
        output reg [31:0] REG11,    // 地址0x0B - 通用寄存器11
        output reg [31:0] REG12,    // 地址0x0C - 通用寄存器12
        output reg [31:0] REG13,    // 地址0x0D - 通用寄存器13
        output reg [31:0] REG14,    // 地址0x0E - 通用寄存器14
        output reg [31:0] REG15     // 地址0x0F - 通用寄存器15
    );

    //=========================================================================
    // 寄存器初始化和更新逻辑
    //=========================================================================
    always @(posedge Clk or negedge Reset_n) begin
        if (!Reset_n) begin
            // 所有寄存器复位为0（用户可根据需要修改默认值）
            REG0  <= 32'h0;
            REG1  <= 32'h0;
            REG2  <= 32'h0;
            REG3  <= 32'h0;
            REG4  <= 32'h0;
            REG5  <= 32'h0;
            REG6  <= 32'h0;
            REG7  <= 32'h0;
            REG8  <= 32'h0;
            REG9  <= 32'h0;
            REG10 <= 32'h0;
            REG11 <= 32'h0;
            REG12 <= 32'h0;
            REG13 <= 32'h0;
            REG14 <= 32'h0;
            REG15 <= 32'h0;
        end
        else if (cmdvalid) begin
            // 根据指令地址更新相应的寄存器（简单直接的映射）
            case (cmd_addr)
                8'h00:
                    REG0  <= cmd_data;
                8'h01:
                    REG1  <= cmd_data;
                8'h02:
                    REG2  <= cmd_data;
                8'h03:
                    REG3  <= cmd_data;
                8'h04:
                    REG4  <= cmd_data;
                8'h05:
                    REG5  <= cmd_data;
                8'h06:
                    REG6  <= cmd_data;
                8'h07:
                    REG7  <= cmd_data;
                8'h08:
                    REG8  <= cmd_data;
                8'h09:
                    REG9  <= cmd_data;
                8'h0A:
                    REG10 <= cmd_data;
                8'h0B:
                    REG11 <= cmd_data;
                8'h0C:
                    REG12 <= cmd_data;
                8'h0D:
                    REG13 <= cmd_data;
                8'h0E:
                    REG14 <= cmd_data;
                8'h0F:
                    REG15 <= cmd_data;
                default:
                    ;  // 其他地址不处理
            endcase
        end
    end

    //=========================================================================
    // 使用建议：
    //
    // 寄存器用途完全由用户定义，例如：
    //
    // REG0  - 控制命令（bit0=start, bit1=stop, bit2=reset等）
    // REG1  - LED控制（低8位控制8个LED）
    // REG2  - 数码管显示值
    // REG3  - DAC输出值
    // REG4  - PWM占空比
    // REG5  - 定时器周期
    // REG6  - 通道选择
    // REG7  - 数据计数
    // ...   - 根据实际应用自定义
    //
    // 在顶层模块中，根据寄存器的值来控制具体功能
    //=========================================================================

endmodule
