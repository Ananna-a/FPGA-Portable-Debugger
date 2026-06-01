//=============================================================================
// 锯齿波ROM查找表 (Sawtooth Wave)
// 8位地址，8位数据
// 波形：从0到255线性递增
//=============================================================================
module sawtooth_rom_a8d8(
        addr,
        clk,
        q
    );

    parameter DATA_WIDTH = 8;
    parameter ADDR_WIDTH = 8;

    input clk;
    input [(ADDR_WIDTH-1):0] addr;
    output reg [(DATA_WIDTH-1):0] q;

    // Declare the ROM variable
    reg [DATA_WIDTH-1:0] rom[2**ADDR_WIDTH-1:0];

    // 生成锯齿波：线性递增 0 -> 255
    integer i;
    initial begin
        for (i = 0; i < 256; i = i + 1) begin
            rom[i] = i[7:0];  // 直接使用地址作为数据
        end
    end

    always @ (posedge clk) begin
        q <= rom[addr];
    end

endmodule
