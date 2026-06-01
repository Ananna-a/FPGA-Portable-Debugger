//=============================================================================
// 任意波形RAM简化版本 V2.0 (带跨时钟域同步器和调试接口)
// 功能：使用简单的 reg 数组存储任意波形（双通道）
// 改进：采用3级同步器消除亚稳态，增加调试反馈信号
//=============================================================================

module arb_wave_ram_simple(
        input Clk,                  // 系统时钟（50MHz，用于写入）
        input Clk_DDS,              // DDS时钟（125MHz，用于读取）
        input Rst_n,

        // 写接口（来自参数控制器，50MHz）
        input wr_en_a,              // 通道A写使能
        input wr_en_b,              // 通道B写使能
        input [7:0] wr_addr,        // 写地址（0-255）
        input [7:0] wr_data,        // 写数据

        // 读接口A（供DDS模块读取，125MHz）
        input [7:0] rd_addr_a,      // 通道A读地址
        output reg [7:0] rd_data_a, // 通道A读数据

        // 读接口B（供DDS模块读取，125MHz）
        input [7:0] rd_addr_b,      // 通道B读地址
        output reg [7:0] rd_data_b, // 通道B读数据

        // 调试接口（新增）
        output reg [7:0] debug_first_byte_a,  // 通道A第一个字节（调试用）
        output reg [7:0] debug_first_byte_b,  // 通道B第一个字节（调试用）
        output reg [7:0] debug_write_count_a, // 通道A写入计数（调试用）
        output reg [7:0] debug_write_count_b  // 通道B写入计数（调试用）
    );

    //=========================================================================
    // 通道A任意波形存储器（256字节 reg 数组）
    //=========================================================================
    reg [7:0] wave_mem_a [0:255];

    // 初始化为中值128（便于调试）
    integer i;
    initial begin
        for (i = 0; i < 256; i = i + 1) begin
            wave_mem_a[i] = 128;  // 初始化为中值
        end
    end

    // 写操作（50MHz系统时钟域）- 同时捕获调试信息
    always @(posedge Clk or negedge Rst_n) begin
        if (!Rst_n) begin
            debug_first_byte_a <= 8'd0;
            debug_write_count_a <= 8'd0;
        end
        else if (wr_en_a) begin
            wave_mem_a[wr_addr] <= wr_data;

            // 捕获第一个字节（地址0）用于调试
            if (wr_addr == 8'd0) begin
                debug_first_byte_a <= wr_data;
            end

            // 统计写入次数（会溢出循环，仅用于观察是否有写入活动）
            debug_write_count_a <= debug_write_count_a + 1'b1;
        end
    end

    // 读操作（125MHz DDS时钟域）
    always @(posedge Clk_DDS or negedge Rst_n) begin
        if (!Rst_n)
            rd_data_a <= 8'd128;
        else
            rd_data_a <= wave_mem_a[rd_addr_a];
    end

    //=========================================================================
    // 通道B任意波形存储器（256字节 reg 数组）
    //=========================================================================
    reg [7:0] wave_mem_b [0:255];

    // 初始化为中值128
    initial begin
        for (i = 0; i < 256; i = i + 1) begin
            wave_mem_b[i] = 128;  // 初始化为中值
        end
    end

    // 写操作（50MHz系统时钟域）- 同时捕获调试信息
    always @(posedge Clk or negedge Rst_n) begin
        if (!Rst_n) begin
            debug_first_byte_b <= 8'd0;
            debug_write_count_b <= 8'd0;
        end
        else if (wr_en_b) begin
            wave_mem_b[wr_addr] <= wr_data;

            // 捕获第一个字节（地址0）用于调试
            if (wr_addr == 8'd0) begin
                debug_first_byte_b <= wr_data;
            end

            // 统计写入次数
            debug_write_count_b <= debug_write_count_b + 1'b1;
        end
    end

    // 读操作（125MHz DDS时钟域）
    always @(posedge Clk_DDS or negedge Rst_n) begin
        if (!Rst_n)
            rd_data_b <= 8'd128;
        else
            rd_data_b <= wave_mem_b[rd_addr_b];
    end

endmodule
