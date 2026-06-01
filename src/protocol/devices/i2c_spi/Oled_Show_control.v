



/*
 * OLED显示控制模块 - 居中两行显示
 * 
 * 显示内容: "芯辰大海点亮未来"
 * 布局方案: 
 *   第1行(Page 2-3): 芯辰大海 (4个字，居中)
 *   第2行(Page 4-5): 点亮未来 (4个字，居中)
 * 
 * 计算说明:
 *   - 屏幕宽度: 128像素
 *   - 字符宽度: 16像素
 *   - 每行4个字: 4×16 = 64像素
 *   - 居中偏移: (128-64)/2 = 32像素
 *   - 第1行Y位置: Page 2 (垂直居中，上方留16像素)
 *   - 第2行Y位置: Page 4 (间隔0页)
 */

module Oled_Show_control(
        input           clk,
        input           rst,
        input           show_ack,
        input           show_req,
        output          show_end,
        output[6:0]     start_x,
        output[3:0]     start_y,
        output[5:0]     show_select     // 显示数据索引
    );

    // 布局参数配置
    localparam  STR_ONE_X_STR   = 7'd32;   // 第1行X起始位置(居中: (128-64)/2=32)
    localparam  STR_ONE_Y_STR   = 4'd2;    // 第1行Y起始位置(Page 2)
    localparam  STR_TWO_X_STR   = 7'd32;   // 第2行X起始位置(居中: (128-64)/2=32)
    localparam  STR_TWO_Y_STR   = 4'd4;    // 第2行Y起始位置(Page 4)
    localparam  STR_TO_STR      = 7'd0;    // 字符间距(紧密排列)
    localparam  STR_WIDTH       = 7'd16;   // 字符宽度(16×16字模)
    localparam  STR_NUM         = 6'd7;    // 总字符数-1 (0-7共8个字)
    localparam  STR_TWO_INDEX   = 6'd4;    // 第2行起始索引(字符4: 点)

    reg[6:0]	start_x_reg;
    reg[7:0]	start_y_reg;
    reg[5:0]	show_select_reg;

    assign start_x = start_x_reg;
    assign start_y = start_y_reg;
    assign show_select = show_select_reg;

    assign show_end = ( show_ack == 1'b1 && show_select_reg == STR_NUM) ? 1'b1 : 1'b0;   //信息全部写完

    always@(posedge clk or negedge rst) begin
        if(rst == 1'b0)
            show_select_reg <= 'd0;
        else if(show_req == 1'b1 && show_ack == 1'b1 && show_select_reg < STR_NUM)
            show_select_reg <= show_select_reg + 1'b1;  // 只在未完成时+1
        else if(show_req == 1'b0)
            show_select_reg <= 'd0;  // show_req结束后才清零
        else
            show_select_reg <= show_select_reg;  // 保持当前值
    end


    always@(posedge clk or negedge rst) begin
        if(rst == 1'b0) begin
            //显示第一行中文字符的起始位置
            start_x_reg <= STR_ONE_X_STR;
            start_y_reg <= STR_ONE_Y_STR;
        end
        else if(show_req == 1'b1 && show_ack == 1'b1) begin
            if(show_select_reg == STR_TWO_INDEX - 1'b1)//显示完第3个字后切换到第二行
            begin
                start_x_reg <= STR_TWO_X_STR;
                start_y_reg <= STR_TWO_Y_STR;
            end
            else begin
                start_x_reg <= start_x_reg + STR_WIDTH + STR_TO_STR;//16是中文字符的大小，0是字符之间的间距
                start_y_reg <= start_y_reg;
            end
        end
        else if(show_req == 1'b1) begin
            start_x_reg <= start_x_reg;
            start_y_reg <= start_y_reg;
        end
        else begin
            //显示第一行中文字符的起始位置
            start_x_reg <= STR_ONE_X_STR;
            start_y_reg <= STR_ONE_Y_STR;
        end
    end
endmodule
