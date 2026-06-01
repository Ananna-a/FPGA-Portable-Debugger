/*
 * 字模数据：芯辰大海点亮未来
 * 字符编号：
 *   0 - 芯
 *   1 - 辰
 *   2 - 大
 *   3 - 海
 *   4 - 点
 *   5 - 亮
 *   6 - 未
 *   7 - 来
 * 
 * 显示布局(128x64 OLED)：
 *   第1行(Page 2-3): 芯辰大海 (居中，起始列32)
 *   第2行(Page 4-5): 点亮未来 (居中，起始列32)
 */

//字符数据_16*16 每个字符16个数据，共两页

module font_data(
        input           clk,
        input           rst,

        input[5:0]      select,      // 字符选择 (0-7)
        input[2:0]      page_cur,    // 当前页 (0-1)
        input[5:0]      index_cur,   // 当前索引

        output[7:0]     data         // 数据输出
    );

    reg[7:0]    data0[31:0];  // 芯
    reg[7:0]    data1[31:0];  // 辰
    reg[7:0]    data2[31:0];  // 大
    reg[7:0]    data3[31:0];  // 海
    reg[7:0]    data4[31:0];  // 点
    reg[7:0]    data5[31:0];  // 亮
    reg[7:0]    data6[31:0];  // 未
    reg[7:0]    data7[31:0];  // 来

    assign data = (select == 'd0) ? data0[(index_cur-'d3) + 'd16 * page_cur] :
           (select == 'd1) ? data1[(index_cur-'d3) + 'd16 * page_cur] :
           (select == 'd2) ? data2[(index_cur-'d3) + 'd16 * page_cur] :
           (select == 'd3) ? data3[(index_cur-'d3) + 'd16 * page_cur] :
           (select == 'd4) ? data4[(index_cur-'d3) + 'd16 * page_cur] :
           (select == 'd5) ? data5[(index_cur-'d3) + 'd16 * page_cur] :
           (select == 'd6) ? data6[(index_cur-'d3) + 'd16 * page_cur] :
           (select == 'd7) ? data7[(index_cur-'d3) + 'd16 * page_cur] : 'd0;

    // 芯 (0)
    always@(posedge clk or negedge rst) begin
        if(rst == 1'b0) begin
            data0[0]  = 8'h04;
            data0[1]  = 8'h04;
            data0[2]  = 8'h04;
            data0[3]  = 8'h04;
            data0[4]  = 8'h1F;
            data0[5]  = 8'h04;
            data0[6]  = 8'h24;
            data0[7]  = 8'h44;
            data0[8]  = 8'h84;
            data0[9]  = 8'h04;
            data0[10] = 8'h1F;
            data0[11] = 8'h04;
            data0[12] = 8'h04;
            data0[13] = 8'h04;
            data0[14] = 8'h04;
            data0[15] = 8'h00;
            data0[16] = 8'h10;
            data0[17] = 8'h08;
            data0[18] = 8'h06;
            data0[19] = 8'h00;
            data0[20] = 8'h00;
            data0[21] = 8'h3F;
            data0[22] = 8'h40;
            data0[23] = 8'h40;
            data0[24] = 8'h40;
            data0[25] = 8'h40;
            data0[26] = 8'h40;
            data0[27] = 8'h70;
            data0[28] = 8'h01;
            data0[29] = 8'h02;
            data0[30] = 8'h0C;
            data0[31] = 8'h00;
        end
    end

    // 辰 (1)
    always@(posedge clk or negedge rst) begin
        if(rst == 1'b0) begin
            data1[0]  = 8'h00;
            data1[1]  = 8'h00;
            data1[2]  = 8'hFE;
            data1[3]  = 8'h82;
            data1[4]  = 8'h92;
            data1[5]  = 8'h92;
            data1[6]  = 8'h92;
            data1[7]  = 8'h92;
            data1[8]  = 8'h92;
            data1[9]  = 8'h92;
            data1[10] = 8'h92;
            data1[11] = 8'h92;
            data1[12] = 8'h92;
            data1[13] = 8'h82;
            data1[14] = 8'h00;
            data1[15] = 8'h00;
            data1[16] = 8'h40;
            data1[17] = 8'h30;
            data1[18] = 8'h0F;
            data1[19] = 8'h00;
            data1[20] = 8'h00;
            data1[21] = 8'hFF;
            data1[22] = 8'h40;
            data1[23] = 8'h20;
            data1[24] = 8'h03;
            data1[25] = 8'h04;
            data1[26] = 8'h08;
            data1[27] = 8'h14;
            data1[28] = 8'h22;
            data1[29] = 8'h40;
            data1[30] = 8'h40;
            data1[31] = 8'h00;
        end
    end

    // 大 (2)
    always@(posedge clk or negedge rst) begin
        if(rst == 1'b0) begin
            data2[0]  = 8'h20;
            data2[1]  = 8'h20;
            data2[2]  = 8'h20;
            data2[3]  = 8'h20;
            data2[4]  = 8'h20;
            data2[5]  = 8'h20;
            data2[6]  = 8'h20;
            data2[7]  = 8'hFF;
            data2[8]  = 8'h20;
            data2[9]  = 8'h20;
            data2[10] = 8'h20;
            data2[11] = 8'h20;
            data2[12] = 8'h20;
            data2[13] = 8'h20;
            data2[14] = 8'h20;
            data2[15] = 8'h00;
            data2[16] = 8'h80;
            data2[17] = 8'h80;
            data2[18] = 8'h40;
            data2[19] = 8'h20;
            data2[20] = 8'h10;
            data2[21] = 8'h0C;
            data2[22] = 8'h03;
            data2[23] = 8'h00;
            data2[24] = 8'h03;
            data2[25] = 8'h0C;
            data2[26] = 8'h10;
            data2[27] = 8'h20;
            data2[28] = 8'h40;
            data2[29] = 8'h80;
            data2[30] = 8'h80;
            data2[31] = 8'h00;
        end
    end

    // 海 (3)
    always@(posedge clk or negedge rst) begin
        if(rst == 1'b0) begin
            data3[0]  = 8'h10;
            data3[1]  = 8'h60;
            data3[2]  = 8'h02;
            data3[3]  = 8'h0C;
            data3[4]  = 8'hC0;
            data3[5]  = 8'h10;
            data3[6]  = 8'h08;
            data3[7]  = 8'hF7;
            data3[8]  = 8'h14;
            data3[9]  = 8'h54;
            data3[10] = 8'h94;
            data3[11] = 8'h14;
            data3[12] = 8'hF4;
            data3[13] = 8'h04;
            data3[14] = 8'h00;
            data3[15] = 8'h00;
            data3[16] = 8'h04;
            data3[17] = 8'h04;
            data3[18] = 8'h7C;
            data3[19] = 8'h03;
            data3[20] = 8'h00;
            data3[21] = 8'h01;
            data3[22] = 8'h1D;
            data3[23] = 8'h13;
            data3[24] = 8'h11;
            data3[25] = 8'h55;
            data3[26] = 8'h99;
            data3[27] = 8'h51;
            data3[28] = 8'h3F;
            data3[29] = 8'h11;
            data3[30] = 8'h01;
            data3[31] = 8'h00;
        end
    end

    // 点 (4)
    always@(posedge clk or negedge rst) begin
        if(rst == 1'b0) begin
            data4[0]  = 8'h00;
            data4[1]  = 8'h00;
            data4[2]  = 8'hC0;
            data4[3]  = 8'h40;
            data4[4]  = 8'h40;
            data4[5]  = 8'h40;
            data4[6]  = 8'h7F;
            data4[7]  = 8'h48;
            data4[8]  = 8'h48;
            data4[9]  = 8'h48;
            data4[10] = 8'h48;
            data4[11] = 8'hC8;
            data4[12] = 8'h08;
            data4[13] = 8'h08;
            data4[14] = 8'h00;
            data4[15] = 8'h00;
            data4[16] = 8'h80;
            data4[17] = 8'h40;
            data4[18] = 8'h37;
            data4[19] = 8'h04;
            data4[20] = 8'h04;
            data4[21] = 8'h14;
            data4[22] = 8'h64;
            data4[23] = 8'h04;
            data4[24] = 8'h14;
            data4[25] = 8'h64;
            data4[26] = 8'h04;
            data4[27] = 8'h07;
            data4[28] = 8'h10;
            data4[29] = 8'hE0;
            data4[30] = 8'h00;
            data4[31] = 8'h00;
        end
    end

    // 亮 (5)
    always@(posedge clk or negedge rst) begin
        if(rst == 1'b0) begin
            data5[0]  = 8'h00;
            data5[1]  = 8'h04;
            data5[2]  = 8'h04;
            data5[3]  = 8'h74;
            data5[4]  = 8'h54;
            data5[5]  = 8'h54;
            data5[6]  = 8'h55;
            data5[7]  = 8'h56;
            data5[8]  = 8'h54;
            data5[9]  = 8'h54;
            data5[10] = 8'h54;
            data5[11] = 8'h74;
            data5[12] = 8'h04;
            data5[13] = 8'h04;
            data5[14] = 8'h00;
            data5[15] = 8'h00;
            data5[16] = 8'h84;
            data5[17] = 8'h83;
            data5[18] = 8'h41;
            data5[19] = 8'h21;
            data5[20] = 8'h1D;
            data5[21] = 8'h05;
            data5[22] = 8'h05;
            data5[23] = 8'h05;
            data5[24] = 8'h05;
            data5[25] = 8'h05;
            data5[26] = 8'h7D;
            data5[27] = 8'h81;
            data5[28] = 8'h81;
            data5[29] = 8'h85;
            data5[30] = 8'hE3;
            data5[31] = 8'h00;
        end
    end

    // 未 (6)
    always@(posedge clk or negedge rst) begin
        if(rst == 1'b0) begin
            data6[0]  = 8'h80;
            data6[1]  = 8'h80;
            data6[2]  = 8'h88;
            data6[3]  = 8'h88;
            data6[4]  = 8'h88;
            data6[5]  = 8'h88;
            data6[6]  = 8'h88;
            data6[7]  = 8'hFF;
            data6[8]  = 8'h88;
            data6[9]  = 8'h88;
            data6[10] = 8'h88;
            data6[11] = 8'h88;
            data6[12] = 8'h88;
            data6[13] = 8'h80;
            data6[14] = 8'h80;
            data6[15] = 8'h00;
            data6[16] = 8'h20;
            data6[17] = 8'h20;
            data6[18] = 8'h10;
            data6[19] = 8'h08;
            data6[20] = 8'h04;
            data6[21] = 8'h02;
            data6[22] = 8'h01;
            data6[23] = 8'hFF;
            data6[24] = 8'h01;
            data6[25] = 8'h02;
            data6[26] = 8'h04;
            data6[27] = 8'h08;
            data6[28] = 8'h10;
            data6[29] = 8'h20;
            data6[30] = 8'h20;
            data6[31] = 8'h00;
        end
    end

    // 来 (7)
    always@(posedge clk or negedge rst) begin
        if(rst == 1'b0) begin
            data7[0]  = 8'h00;
            data7[1]  = 8'h08;
            data7[2]  = 8'h08;
            data7[3]  = 8'h28;
            data7[4]  = 8'hC8;
            data7[5]  = 8'h08;
            data7[6]  = 8'h08;
            data7[7]  = 8'hFF;
            data7[8]  = 8'h08;
            data7[9]  = 8'h08;
            data7[10] = 8'h88;
            data7[11] = 8'h68;
            data7[12] = 8'h08;
            data7[13] = 8'h08;
            data7[14] = 8'h00;
            data7[15] = 8'h00;
            data7[16] = 8'h21;
            data7[17] = 8'h21;
            data7[18] = 8'h11;
            data7[19] = 8'h11;
            data7[20] = 8'h09;
            data7[21] = 8'h05;
            data7[22] = 8'h03;
            data7[23] = 8'hFF;
            data7[24] = 8'h03;
            data7[25] = 8'h05;
            data7[26] = 8'h09;
            data7[27] = 8'h11;
            data7[28] = 8'h11;
            data7[29] = 8'h21;
            data7[30] = 8'h21;
            data7[31] = 8'h00;
        end
    end


endmodule
