// DS18B20温度显示转换模块
// 功能：将16位温度原始数据转换为BCD码用于数码管显示
// 参考：DS18B20第二版/ctrl.v

module ds18b20_temp_display (
        input wire [15:0] temp_raw,      // 温度原始数据（DS18B20格式）
        input wire [1:0] precision,      // 精度选择：2'b11=12位(0.0625°C)
        output reg [23:0] bcd_data       // 6位BCD码输出（符号+十位+个位+小数3位）
    );

    // 温度数据解析
    wire sign = temp_raw[11];                    // 符号位（1=负温度）
    wire [10:0] temp_abs = temp_raw[10:0];       // 温度绝对值部分
    wire [6:0] temp_integer = temp_abs[10:4];    // 整数部分（7位）
    wire [3:0] temp_fraction = temp_abs[3:0];    // 小数部分（4位）

    // 精度转换系数（单位：0.0001°C）
    // 12位精度：0.0625°C = 625 * 0.0001°C
    reg [12:0] fraction_scale;
    always @(*) begin
        case (precision)
            2'b11:
                fraction_scale = 13'd625;   // 12位精度 0.0625°C
            2'b10:
                fraction_scale = 13'd1250;  // 11位精度 0.125°C
            2'b01:
                fraction_scale = 13'd2500;  // 10位精度 0.25°C
            2'b00:
                fraction_scale = 13'd5000;  // 9位精度  0.5°C
        endcase
    end

    // 小数部分转换为十进制
    // temp_fraction * fraction_scale = 实际小数值（单位0.0001°C）
    wire [16:0] fraction_value = temp_fraction * fraction_scale;  // 最大15*625=9375

    // 提取小数各位（最多3位小数）
    wire [3:0] frac_digit1 = fraction_value / 1000 % 10;        // 小数第1位（0.1°C）
    wire [3:0] frac_digit2 = fraction_value / 100 % 10;         // 小数第2位（0.01°C）
    wire [3:0] frac_digit3 = fraction_value / 10 % 10;          // 小数第3位（0.001°C）

    // 整数部分BCD转换
    wire [3:0] int_digit1 = temp_integer % 10;                  // 个位
    wire [3:0] int_digit10 = temp_integer / 10 % 10;            // 十位

    // 符号显示编码（使用hex8_ext扩展编码）
    // 正温度不显示符号（用空白），负温度显示负号
    wire [3:0] sign_code = sign ? 4'hD : 4'hE;  // D=负号'-' (seg[6]横线), E=空白(全灭)

    // 组合输出（从右到左：小数3位+小数2位+小数1位+个位+十位+符号）
    // 数码管位序：[0]=小数3位, [1]=小数2位, [2]=小数1位, [3]=个位, [4]=十位, [5]=符号
    always @(*) begin
        bcd_data = {sign_code, int_digit10, int_digit1, frac_digit1, frac_digit2, frac_digit3};
    end

endmodule

