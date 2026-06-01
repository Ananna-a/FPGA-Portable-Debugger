
//显示信息   16*16
module Oled_Show_Info(
        input		clk,
        input		rst,

        input		write_done,		//清除一组数据完成

        //起始坐标
        input[6:0]		start_x,
        input[3:0]		start_y,

        input[5:0]	show_select,			//显示数据索引

        input		show_req,		//显示请求
        output		show_ack,		//显示完成

        output[23:0]	 show_data		//清除数据的命令
    );

    localparam			RST_T 			=	1'b0;				//复位有效
    localparam			STR_WIDTH      =6'd16;//字符宽大小
    localparam			SHOW_INDEX_SET =(STR_WIDTH+3)-1;//6'd18
    localparam			SHOW_PAGE_SET  =3'd1;//16/2-1=1

    reg[5:0]	show_index;
    reg[2:0]	show_page;
    reg[23:0]	show_data_reg;

    wire[7:0]	data;


    assign show_data = show_data_reg;
    assign show_ack = (show_index >= SHOW_INDEX_SET && show_page >= SHOW_PAGE_SET && write_done == 1'b1) ? 1'b1 : 1'b0;//完成一个中文字符写入

    always@(posedge clk or negedge rst) begin
        if(rst == RST_T)
            show_index <= 'd0;
        else if(show_index ==  SHOW_INDEX_SET && write_done == 1'b1 )
            show_index <= 'd0;
        else if(write_done == 1'b1 && show_req == 1'b1)
            show_index <= show_index + 1'b1;
        else
            show_index <=show_index;
    end
    //设置页
    always@(posedge clk or negedge rst) begin
        if(rst == RST_T)
            show_page <= 'd0;
        else if(show_index == SHOW_INDEX_SET && write_done == 1'b1 && show_page == SHOW_PAGE_SET)
            show_page <= 'd0;
        else if(show_index == SHOW_INDEX_SET && write_done == 1'b1)
            show_page <=show_page + 1'b1;
        else
            show_page <= show_page;
    end

    always@(*) begin
        case(show_index)
            'd0:
                show_data_reg <= {8'h78,8'h00,8'hb0 + show_page+start_y};
            'd1:
                show_data_reg <= {8'h78,8'h00,8'h00 +start_x[3:0]};
            'd2:
                show_data_reg <= {8'h78,8'h00,8'h10 + start_x[6:4]};
            default:
                show_data_reg <= {8'h78,8'h40, data};
        endcase
    end


    font_data font_data_HP(

                  .clk	(clk),
                  .rst	(rst),

                  .select (show_select),	//字符选择
                  .page_cur (show_page),	//当前页
                  .index_cur (show_index),	//当前行

                  .data (data)		//数据输出
              );


endmodule
