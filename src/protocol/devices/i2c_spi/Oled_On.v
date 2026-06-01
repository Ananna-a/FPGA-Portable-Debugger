




//oled屏幕全亮模块
module Oled_On(
        input		clk,
        input		rst,

        input		write_done,		//清除一组数据完成

        input		On_req,		//清除请求
        output		On_ack,		//清除完成

        output[23:0]	 On_data		//清除数据的命令
    );

    localparam			RST_T			=	1'b0;				//复位有效

    reg[23:0]		On_data_reg;


    reg[3:0]	On_page;
    reg[7:0]	On_index;

    assign On_data  = On_data_reg;
    assign On_ack = (On_index >= 'd130 && On_page >= 'd7 && write_done == 1'b1) ? 1'b1 : 1'b0;//初始化完成信号

    always@(posedge clk or negedge rst) begin
        if(rst == RST_T)
            On_index <= 'd0;
        else if(On_index == 'd130 && write_done == 1'b1 )
            On_index <= 'd0;
        else if(write_done == 1'b1 && On_req == 1'b1)
            On_index <= On_index + 1'b1;
        else
            On_index <=On_index;
    end

    //设置页
    always@(posedge clk or negedge rst) begin
        if(rst == RST_T)
            On_page <= 'd0;
        else if(On_index == 'd130 && write_done == 1'b1 && On_page == 'd7)
            On_page <= 'd0;
        else if(On_index == 'd130 && write_done == 1'b1)
            On_page <=On_page + 1'b1;
        else
            On_page <= On_page;
    end
    always@(*) begin
        case(On_index)
            'd0:
                On_data_reg <= {8'h78,8'h00,8'hb0 + On_page};
            'd1:
                On_data_reg <= {8'h78,8'h00,8'h00};
            'd2:
                On_data_reg <= {8'h78,8'h00,8'h10};
            default:
                On_data_reg <= {8'h78,8'h40,8'hFF};
        endcase

    end


endmodule
