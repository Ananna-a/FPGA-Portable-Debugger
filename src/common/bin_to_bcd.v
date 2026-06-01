//=============================================================================
// 二进制转BCD模块（用于数码管显示）
// 功能：将32位二进制数转换为8位BCD编码（每位4bit，支持0-9999 9999显示）
// 方法：Double-Dabble算法（移位3加法）
// 日期：2025-11-04
//=============================================================================

module bin_to_bcd(
        input  wire        clk,
        input  wire        rst_n,
        input  wire [31:0] binary,      // 输入二进制数
        input  wire        convert_en,  // 转换使能（脉冲）
        output reg  [31:0] bcd,         // 输出BCD码（8个4位BCD数字）
        output reg         done         // 转换完成标志
    );

    //=========================================================================
    // 状态机定义
    //=========================================================================
    localparam IDLE    = 2'd0;  // 空闲
    localparam SHIFT   = 2'd1;  // 移位
    localparam DONE    = 2'd2;  // 完成

    reg [1:0]  state;
    reg [5:0]  shift_cnt;       // 移位计数器（需要32次移位）
    reg [63:0] shift_reg;       // 移位寄存器（低32位=二进制，高32位=BCD）
    reg [63:0] shift_reg_add3;  // 加3后的临时寄存器

    //=========================================================================
    // Double-Dabble算法实现（修正版：组合逻辑加3，时序逻辑移位）
    //=========================================================================

    // 组合逻辑：检查每个BCD位，如果>=5则加3
    always @(*) begin
        shift_reg_add3 = shift_reg;

        if (shift_reg[35:32] >= 4'd5)
            shift_reg_add3[35:32] = shift_reg[35:32] + 4'd3;
        if (shift_reg[39:36] >= 4'd5)
            shift_reg_add3[39:36] = shift_reg[39:36] + 4'd3;
        if (shift_reg[43:40] >= 4'd5)
            shift_reg_add3[43:40] = shift_reg[43:40] + 4'd3;
        if (shift_reg[47:44] >= 4'd5)
            shift_reg_add3[47:44] = shift_reg[47:44] + 4'd3;
        if (shift_reg[51:48] >= 4'd5)
            shift_reg_add3[51:48] = shift_reg[51:48] + 4'd3;
        if (shift_reg[55:52] >= 4'd5)
            shift_reg_add3[55:52] = shift_reg[55:52] + 4'd3;
        if (shift_reg[59:56] >= 4'd5)
            shift_reg_add3[59:56] = shift_reg[59:56] + 4'd3;
        if (shift_reg[63:60] >= 4'd5)
            shift_reg_add3[63:60] = shift_reg[63:60] + 4'd3;
    end

    // 时序逻辑：状态机控制
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            shift_cnt <= 6'd0;
            shift_reg <= 64'd0;
            bcd <= 32'd0;
            done <= 1'b0;
        end
        else begin
            case (state)
                //-------------------------------------------------------------
                // IDLE: 等待转换使能
                //-------------------------------------------------------------
                IDLE: begin
                    done <= 1'b0;
                    if (convert_en) begin
                        shift_reg <= {32'd0, binary};  // 初始化移位寄存器
                        shift_cnt <= 6'd0;
                        state <= SHIFT;
                    end
                end

                //-------------------------------------------------------------
                // SHIFT: 执行加3和移位操作
                //-------------------------------------------------------------
                SHIFT: begin
                    if (shift_cnt < 32) begin
                        // 左移一位（使用加3后的值）
                        shift_reg <= shift_reg_add3 << 1;
                        shift_cnt <= shift_cnt + 1;
                    end
                    else begin
                        // 移位完成，提取BCD结果
                        bcd <= shift_reg[63:32];
                        state <= DONE;
                    end
                end

                //-------------------------------------------------------------
                // DONE: 转换完成
                //-------------------------------------------------------------
                DONE: begin
                    done <= 1'b1;
                    state <= IDLE;
                end

                default:
                    state <= IDLE;
            endcase
        end
    end

    //=========================================================================
    // 使用说明：
    //
    // 1. 输入范围：0 - 4294967295（32位无符号数）
    // 2. 输出格式：bcd[31:28]=千万位, bcd[27:24]=百万位, ..., bcd[3:0]=个位
    // 3. 时序：
    //    - 拉高convert_en一个周期
    //    - 等待约34个周期
    //    - done拉高时，bcd输出有效
    //
    // 4. 示例：
    //    输入: binary = 12345678
    //    输出: bcd = 0x12345678 (BCD编码)
    //           显示: 1234 5678
    //
    // 5. 注意：
    //    - 超过99999999的数值会溢出（最高位被截断）
    //    - 如需显示更大数值，扩展shift_reg和bcd宽度
    //=========================================================================

endmodule
