// hex8扩展模块 - 支持更多字符显示
// 在原hex8基础上添加对H、P等特殊字符的支持

module hex8_ext(
        clk,
        reset_n,
        en,
        disp_data,
        sel_mask,
        dot_mask,
        sel,
        seg
    );
    wire reset=~reset_n;
    input clk;	//50M
    input reset_n;
    input en;	//数码管显示使能，1使能，0关闭

    input [31:0]disp_data;
    input [7:0]sel_mask;  // 位选掩码，1=显示该位，0=关闭该位
    input [7:0]dot_mask;  // 小数点掩码，1=显示小数点，0=不显示

    output [7:0] sel;//数码管位选（选择当前要显示的数码管）
    output reg [7:0] seg;//数码管段选（当前要显示的内容+小数点DP）

    reg [14:0]divider_cnt;//25000-1

    reg clk_1K;
    reg [7:0]sel_r;

    reg [3:0]data_tmp;//数据缓存

    //	分频计数器计数模块
    always@(posedge clk or posedge reset)
        if(reset)
            divider_cnt <= 15'd0;
        else if(!en)
            divider_cnt <= 15'd0;
        else if(divider_cnt == 24999)
            divider_cnt <= 15'd0;
        else
            divider_cnt <= divider_cnt + 1'b1;

    //1K扫描时钟生成模块
    always@(posedge clk or posedge reset)
        if(reset)
            clk_1K <= 1'b0;
        else if(divider_cnt == 24999)
            clk_1K <= ~clk_1K;
        else
            clk_1K <= clk_1K;

    //8位循环移位寄存器
    always@(posedge clk_1K or posedge reset)
        if(reset)
            sel_r <= 8'b0000_0001;
        else if(sel_r == 8'b1000_0000)
            sel_r <= 8'b0000_0001;
        else
            sel_r <=  sel_r << 1;

    always@(*)
    case(sel_r)
        8'b0000_0001:
            data_tmp = disp_data[3:0];
        8'b0000_0010:
            data_tmp = disp_data[7:4];
        8'b0000_0100:
            data_tmp = disp_data[11:8];
        8'b0000_1000:
            data_tmp = disp_data[15:12];
        8'b0001_0000:
            data_tmp = disp_data[19:16];
        8'b0010_0000:
            data_tmp = disp_data[23:20];
        8'b0100_0000:
            data_tmp = disp_data[27:24];
        8'b1000_0000:
            data_tmp = disp_data[31:28];
        default:
            data_tmp = 4'b0000;
    endcase

    // 扩展段码表 - 支持更多字符
    // 标准4位编码 + 扩展字符映射
    // 扩展方案：使用高位值作为特殊字符
    // 4'h0-4'h9: 数字0-9（正常BCD编码）
    // 4'hA: 字母'A'
    // 4'hB: 字母'H'(用于HIFPGA显示)
    // 4'hC: 字母'P'(用于HIFPGA显示)
    // 4'hD: 负号'-'(只有中间横线) - 用于负温度
    // 4'hE: 空白(全灭) - 用于正温度符号位
    // 4'hF: 字母'F'

    // ⚠️ 修复：BCD转换后不应该出现A-F，但为了健壮性，将A-F都显示为0
    reg [6:0] seg_base;  // 基础7段码
    reg dot_bit;         // 小数点位

    always@(*) begin
        // 根据当前sel_r确定小数点状态
        case(sel_r)
            8'b0000_0001:
                dot_bit = dot_mask[0];
            8'b0000_0010:
                dot_bit = dot_mask[1];
            8'b0000_0100:
                dot_bit = dot_mask[2];
            8'b0000_1000:
                dot_bit = dot_mask[3];
            8'b0001_0000:
                dot_bit = dot_mask[4];
            8'b0010_0000:
                dot_bit = dot_mask[5];
            8'b0100_0000:
                dot_bit = dot_mask[6];
            8'b1000_0000:
                dot_bit = dot_mask[7];
            default:
                dot_bit = 1'b0;
        endcase

        // 段码查找
        case(data_tmp)
            4'h0:
                seg_base = 7'b1000000;  // 0
            4'h1:
                seg_base = 7'b1111001;  // 1 (I)
            4'h2:
                seg_base = 7'b0100100;  // 2
            4'h3:
                seg_base = 7'b0110000;  // 3
            4'h4:
                seg_base = 7'b0011001;  // 4
            4'h5:
                seg_base = 7'b0010010;  // 5
            4'h6:
                seg_base = 7'b0000010;  // 6
            4'h7:
                seg_base = 7'b1111000;  // 7
            4'h8:
                seg_base = 7'b0000000;  // 8
            4'h9:
                seg_base = 7'b0010000;  // 9
            4'ha:
                seg_base = 7'b0001000;  // A
            4'hb:
                seg_base = 7'b0001001;  // H (扩展)
            4'hc:
                seg_base = 7'b0001100;  // P (扩展)
            4'hd:
                seg_base = 7'b0111111;  // 负号'-' (只有中间横线seg[6])
            4'he:
                seg_base = 7'b1111111;  // 空白(全灭) - 用于正温度符号位
            4'hf:
                seg_base = 7'b0001110;  // F
            default:
                seg_base = 7'b1000000;  // 默认显示0（健壮性处理）
        endcase

        // 组合段码和小数点：seg[7]=DP, seg[6:0]=7段
        seg = {~dot_bit, seg_base};  // DP低电平点亮，所以取反
    end

    assign sel = (en)?(sel_r & sel_mask):8'b0000_0000;  // 使用位选掩码控制哪些位显示

endmodule
