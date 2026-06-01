








//oled屏幕清除模块
module Oled_Clear(
        input		clk,
        input		rst,

        input		write_done,		//清除�?组数据完�?

        input		clear_req,		//清除请求
        output		clear_ack,		//清除完成

        output[23:0]	 clear_data		//清除数据的命�?
    );

    localparam			RST_T			=	1'b0;				//复位有效

    reg[23:0]		clear_data_reg;


    reg[3:0]	clear_page;
    reg[7:0]	clear_index;

    assign clear_data  = clear_data_reg;
    assign clear_ack = (clear_index >= 'd130 && clear_page >= 'd7 && write_done == 1'b1) ? 1'b1 : 1'b0;//初始化完成信�?


    always@(posedge clk or negedge rst) begin
        if(rst == RST_T)
            clear_index <= 'd0;
        else if(clear_index == 'd130 && write_done == 1'b1 )
            clear_index <= 'd0;
        else if(write_done == 1'b1 && clear_req == 1'b1)
            clear_index <= clear_index + 1'b1;
        else
            clear_index <= clear_index;
    end

    always@(posedge clk or negedge rst) begin
        if(rst == RST_T)
            clear_page <= 'd0;
        else if(clear_index == 'd130 && write_done == 1'b1 && clear_page == 'd7)
            clear_page <= 'd0;
        else if(clear_index == 'd130 && write_done == 1'b1)
            clear_page <= clear_page + 1'b1;
        else
            clear_page <= clear_page;
    end
    always@(*) begin
        case(clear_index)
            'd0:
                clear_data_reg <= {8'h78,8'h00,8'hb0 + clear_page};
            'd1:
                clear_data_reg <= {8'h78,8'h00,8'h00};
            'd2:
                clear_data_reg <= {8'h78,8'h00,8'h10};
            default:
                clear_data_reg <= {8'h78,8'h40,8'h00};
        endcase

    end




endmodule
