`timescale 1ns / 1ps
//
// Company:
// Engineer:
//
// Create Date:    22:40:45 11/20/2017
// Design Name:
// Module Name:    I2C_Master
// Project Name:
// Target Devices:
// Tool versions:
// Description:
/*
I2C����ͨ��Э��ͨ��ģ�飺SCL SDA
��źţ�SCL��ʱ��SDA����
����źţ�SCL��ʱ��SDA����
SDA������SCL�͵�ƽʱ��λ
���ʵ��Ĭ�Ͽ�ʼ�ź�������ź���SCL�ߵ�ƽ�м����
SDA����λ�ı���SCL�͵�ƽ���м����
SCLʱ��Ƶ��Ϊ200kHz
���ַ�ɵ���ģ���֧�ֶ�Ҳ֧��д��ͨ������ܽſ���
*/
//
// Dependencies:
//
// Revision:
// Revision 0.01 - File Created
// Additional Comments:
//
//

module I2C_Master(
        I_Clk_in,
        I_Rst_n,
        O_SCL,
        IO_SDA,
        //control_sig
        I_Start,   //һ�ζ�/д������ʼ�ź�
        O_Done,    //һ�ζ�/д���������ź�
        I_R_W_SET, //��д�����źţ�дΪ1����Ϊ0
        I_Slave_Addr,//�ӻ���ַ
        I_R_W_Data,//��д������16λI_R_W_Data[15:8]->reg_addr,I_R_W_Data[7:0]->W_data,��״̬���Ĭ��Ϊ7'b0
        O_Data,    //���������ݣ���O_Done����ʱ������Ч
        O_Error	  //��⴫������źţ������ִӻ�δ��Ӧ���ӻ����ܽ������ݵ����ʱ�����ߵ�ƽ
    );

    //I/O
    input		I_Clk_in;
    input		I_Rst_n;
    output		O_SCL;
    inout		IO_SDA;

    input		I_Start;
    output		O_Done;
    input  [6:0] 	I_Slave_Addr;
    input		I_R_W_SET;
    input  [15:0]	I_R_W_Data;
    output [7:0] 	O_Data;
    output      	O_Error;
    /******ʱ�Ӷ�λģ�飨����ʱʱ��Ϊ50MHz��,��λSCL�ĸߵ�ƽ���ģ���SCL�ĵ͵�ƽ���ģ�����100kHz��SCL*******/
    parameter   Start_Delay=9'd60;//��ʼʱSDA���͵�ƽ������ʱ�䣬���ü�������ӦС��SCL_HIGH2LOW-1
    parameter   Stop_Delay=9'd150;//һ�ζ�/д������SDA���ߵ�ƽ��ʱ�䣬���ü�������ӦС��SCL_HIGH2LOW-1
    parameter   SCL_Period=9'd499;//���԰�ʱ��Ϊ50MHz,100KHzΪ500��Clk
    parameter   SCL_LOW_Dest=9'd374;//ʱ���ж��ߵ�ƽ��ǰ���͵�ƽ�ں�,�͵�ƽ����Ϊ3/4�����ڣ�375��Clk
    parameter   SCL_HIGH2LOW=9'd249;//��ƽ��תλ�ã�1/2��SCL���ڣ�250��Clk
    parameter   ACK_Dect=9'd124;     //SCL�ߵ�ƽ�м�λ�ã����ڼ��ACK�ź�
    reg    [8:0]	R_SCL_Cnt;
    reg         	R_SCL_En;

    assign      	O_SCL=(R_SCL_Cnt<=SCL_HIGH2LOW)?1'b1:1'b0;//SCL ʱ�����

    always @ (posedge I_Clk_in or negedge I_Rst_n) begin
        if (~I_Rst_n) begin
            R_SCL_Cnt<=9'b0;
        end
        else begin
            if (R_SCL_En)
                if (R_SCL_Cnt==SCL_Period)
                    R_SCL_Cnt<=9'b0;
                else
                    R_SCL_Cnt<=R_SCL_Cnt+9'b1;
            else
                R_SCL_Cnt<=9'b0;
        end
    end

    /******SDA��д����ģ��******/
    reg [5:0]    R_State;
    reg          R_SDA_I_O_SET;//SDA˫��ѡ��I/O�� 1Ϊ�����0Ϊ����
    reg          R_SDA_t;      //SDA������˿�
    reg          O_Done;       //�����ź�
    reg [7:0]    O_Data;       //����������
    reg          O_Error;		//�������ָʾ�ź�

    /****״̬����*****/
    parameter    Start=6'd0;  //һ�ζ�д��ʼ��״̬
    parameter    ReStart=6'd34; //���������״̬
    parameter    Stop=6'd56;    //����ֹͣλ״̬

    always @ (posedge I_Clk_in or negedge I_Rst_n) begin
        if (~I_Rst_n) begin
            R_SCL_En<=1'b0;     //����ʱ��ֹͣ
            R_State<=6'd0;
            R_SDA_I_O_SET<=1'b1;//Ĭ������Ϊ����ܽ�
            R_SDA_t<=1'b1;      //SDA���Ĭ������
            O_Data<=8'b0;
            O_Done<=1'b0;
            O_Error<=1'b0;
        end
        else begin
            if (I_Start) //����ʼ�ź��ø�ʱ��ʾI2Cͨ�ſ�ʼ
            begin
                case(R_State)
                    Start:   //����λ
                    begin
                        R_SCL_En<=1'b1;
                        O_Error<=1'b0;//ÿ��������һ�δ���ʱ����������־λ
                        if (R_SCL_Cnt==Start_Delay) begin
                            R_SDA_t<=1'b0; //SCL�ߵ�ƽʱ����
                            R_State<=R_State+6'd1;
                        end
                        else begin
                            R_SDA_t<=1'b1;
                            R_State<=R_State;
                        end
                    end
                    6'd1,6'd2,6'd3,6'd4,6'd5,6'd6,6'd7:  //д��7λ�ӻ���ַ
                    begin
                        if (R_SCL_Cnt==SCL_LOW_Dest) begin
                            R_SDA_t<=I_Slave_Addr[6'd7-R_State];//��MSB-LSBд������˴ӻ���ַ
                            R_State<=R_State+6'd1;
                        end
                        else
                            R_State<=R_State;
                    end
                    6'd8: //д��д��־��0��
                    begin
                        if (R_SCL_Cnt==SCL_LOW_Dest) begin
                            R_SDA_t<=1'b0;
                            R_State<=R_State+6'd1;
                        end
                        else
                            R_State<=R_State;
                    end
                    6'd9: //ACK״̬
                    begin
                        if (R_SCL_Cnt==SCL_HIGH2LOW) //�ڵ�8��ʱ�ӵ��½����ͷ�SDA
                        begin
                            R_SDA_I_O_SET<=1'b0;
                            R_State<=R_State+6'd1;
                        end
                        else
                            R_State<=R_State;
                    end
                    6'd10: //�ڵ�9��ʱ�Ӹߵ�ƽ���ļ��ACK�ź��Ƿ�Ϊ0�����Ϊ1�����ʾ�ӻ�δӦ�𣬽������λ
                    begin
                        if (R_SCL_Cnt==ACK_Dect) begin
                            O_Error<=IO_SDA;  //���ӻ��Ƿ���Ӧ
                            R_State<=R_State+6'd1;
                        end
                        else
                            R_State<=R_State;
                    end
                    6'd11: begin
                        if (R_SCL_Cnt==SCL_HIGH2LOW) //�ڵ�9��ʱ�ӵ��½�������ռ��SDA��׼�����ʹӻ��ӼĴ�����ַ
                        begin
                            R_SDA_I_O_SET<=1'b1;
                            R_State<=(O_Error)?Stop:(R_State+6'd1);
                            R_SDA_t<=1'b0;
                        end
                        else
                            R_State<=R_State;
                    end
                    6'd12,6'd13,6'd14,6'd15,6'd16,6'd17,6'd18,6'd19:  //д��8λ�Ĵ�����ַ
                    begin
                        if (R_SCL_Cnt==SCL_LOW_Dest) begin
                            R_SDA_t<=I_R_W_Data[6'd27-R_State];//��MSB-LSBд��Ĵ�����ַ I_R_W_Data[15:8]
                            R_State<=R_State+6'd1;
                        end
                        else
                            R_State<=R_State;
                    end
                    6'd20: //ACK״̬
                    begin
                        if (R_SCL_Cnt==SCL_HIGH2LOW)//�ڵ�8��ʱ�ӵ��½����ͷ�SDA
                        begin
                            R_SDA_I_O_SET<=1'b0;
                            R_State<=R_State+6'd1;
                        end
                        else
                            R_State<=R_State;
                    end
                    6'd21: //���ACK
                    begin
                        if (R_SCL_Cnt==ACK_Dect) begin
                            O_Error<=IO_SDA;//���ӻ��Ƿ���Ӧ
                            R_State<=R_State+6'd1;
                        end
                        else
                            R_State<=R_State;
                    end
                    6'd22: begin
                        if (R_SCL_Cnt==SCL_HIGH2LOW) //�ڵ�9��ʱ�ӵ��½�������ռ��SDA�����ֽ������÷������ݻ��Ƕ�����
                        begin
                            R_SDA_I_O_SET<=1'b1;
                            R_State<=(O_Error)?Stop:((I_R_W_SET)?(R_State+6'd1):ReStart); //�ӻ�״̬
                            R_SDA_t<=(O_Error|I_R_W_SET)?1'b0:1'b1; //�˴�����SDA�ź���Ϊ��״̬������ʼ�ź���׼��
                        end
                        else
                            R_State<=R_State;
                    end
                    6'd23,6'd24,6'd25,6'd26,6'd27,6'd28,6'd29,6'd30://д��8λ���ݵ�ַ
                    begin
                        if (R_SCL_Cnt==SCL_LOW_Dest) begin
                            R_SDA_t<=I_R_W_Data[6'd30-R_State];//��MSB-LSBд��8λ���ݵ�ַ
                            R_State<=R_State+6'd1;
                        end
                        else
                            R_State<=R_State;
                    end
                    6'd31: //ACK״̬
                    begin
                        if (R_SCL_Cnt==SCL_HIGH2LOW)//�ڵ�8��ʱ�ӵ��½����ͷ�SDA
                        begin
                            R_SDA_I_O_SET<=1'b0;
                            R_State<=R_State+6'd1;
                        end
                        else
                            R_State<=R_State;
                    end
                    6'd32://���ACK
                    begin
                        if (R_SCL_Cnt==ACK_Dect) begin
                            O_Error<=IO_SDA;//���ӻ��Ƿ���Ӧ
                            R_State<=R_State+6'd1;
                        end
                        else
                            R_State<=R_State;
                    end
                    6'd33: begin
                        if (R_SCL_Cnt==SCL_HIGH2LOW)//�ڵ�9��ʱ�ӵ��½�������ռ��SDA��׼������ֹͣλ
                        begin
                            R_SDA_I_O_SET<=1'b1;
                            R_SDA_t<=1'b0;//������SDA�ź�
                            R_State<=Stop;//��ת������λ����״̬
                        end
                        else
                            R_State<=R_State;
                    end
                    ReStart://������״̬��� ��ʼʱ��Ҫ������ʼ״̬
                    begin
                        if (R_SCL_Cnt==Start_Delay) begin
                            R_SDA_t<=1'b0; //SCL�ߵ�ƽʱ����
                            R_State<=R_State+6'd1;
                        end
                        else begin
                            R_SDA_t<=1'b1;
                            R_State<=R_State;
                        end
                    end
                    6'd35,6'd36,6'd37,6'd38,6'd39,6'd40,6'd41://���ʹӻ�7λ��ַ
                    begin
                        if (R_SCL_Cnt==SCL_LOW_Dest) begin
                            R_SDA_t<=I_Slave_Addr[6'd41-R_State];//��MSB-LSBд������˴ӻ���ַ
                            R_State<=R_State+6'd1;
                        end
                        else
                            R_State<=R_State;
                    end
                    6'd42://д�����־(1)
                    begin
                        if (R_SCL_Cnt==SCL_LOW_Dest) begin
                            R_SDA_t<=1'b1;//д�����ַ��־
                            R_State<=R_State+6'd1;
                        end
                        else
                            R_State<=R_State;
                    end
                    6'd43: //ACK״̬
                    begin
                        if (R_SCL_Cnt==SCL_HIGH2LOW)//�ڵ�8��ʱ�ӵ��½����ͷ�SDA
                        begin
                            R_SDA_I_O_SET<=1'b0;
                            R_State<=R_State+6'd1;
                        end
                        else
                            R_State<=R_State;
                    end
                    6'd44://ACK���
                    begin
                        if (R_SCL_Cnt==ACK_Dect) begin
                            O_Error<=IO_SDA;
                            R_State<=R_State+6'd1;
                        end
                        else
                            R_State<=R_State;
                    end
                    6'd45://֮����Ҫһֱ��ȡ���ݣ�����SDA����������Ҫ��������״̬
                    begin
                        if (R_SCL_Cnt==SCL_HIGH2LOW)//�ڵ�9��ʱ���½��ر���SDA���ߵ��ͷ�״̬
                        begin
                            R_SDA_I_O_SET<=(O_Error)?1'b1:1'b0;//��ǰ��ACK���ͨ�����򱣳�SDA�����ͷ�״̬����                                                                                ͨ����ռ��SDA������������ֹͣλ
                            R_State<=(O_Error)?Stop:(R_State+6'd1);
                            R_SDA_t<=1'b0;
                        end
                        else
                            R_State<=R_State;
                    end
                    6'd46,6'd47,6'd48,6'd49,6'd50,6'd51,6'd52,6'd53://8��ʱ���źŸߵ�ƽ�м����δ�SDA�϶�ȡ����
                    begin
                        if (R_SCL_Cnt==ACK_Dect) begin
                            O_Data<={O_Data[6:0],IO_SDA};//��MSB��ʼ��������
                            R_State<=R_State+6'd1;
                        end
                        else
                            R_State<=R_State;
                    end
                    6'd54://����8λ���ݺ�,������Ҫ���ⷢ��һ��NACK�ź�
                    begin
                        if (R_SCL_Cnt==SCL_HIGH2LOW) begin
                            R_SDA_I_O_SET<=1'b1;//��������ռ��SDA
                            R_SDA_t<=1'b1;
                            R_State<=R_State+6'd1;
                        end
                        else
                            R_State<=R_State;
                    end
                    6'd55://�ڵ�9��ʱ���½��س���ռ�����ߣ�����SDA����ʼ���ͽ���λ
                    begin
                        if (R_SCL_Cnt==SCL_HIGH2LOW) begin
                            R_SDA_t<=1'b0;
                            R_State<=R_State+6'd1;
                        end
                        else
                            R_State<=R_State;
                    end
                    Stop: //����ֹͣλ
                    begin
                        if (R_SCL_Cnt==Stop_Delay) begin
                            R_SDA_t<=1'b1;
                            R_State<=R_State+6'd1;
                        end
                        else
                            R_State<=R_State;
                    end
                    6'd57: //ֹͣʱ�ӣ�ͬʱ���Done�źţ���ʾһ�ζ�д�������
                    begin
                        R_SCL_En<=1'b0;
                        O_Done<=1'b1;//����Done�ź�
                        R_State<=R_State+6'd1;
                    end
                    6'd58: begin
                        O_Done<=1'b0;//����Done�ź�
                        R_State<=Start;
                    end
                    default: begin
                        R_SCL_En<=1'b0;//����ʱ��ֹͣ
                        R_State<=6'd0;
                        R_SDA_I_O_SET<=1'b1;//Ĭ������Ϊ����ܽ�
                        R_SDA_t<=1'b1;//SDA���Ĭ������
                        O_Done<=1'b0;
                    end
                endcase
            end
            else         //��ʼ�ź���Чʱ���ص���ʼ����
            begin
                R_SCL_En<=1'b0;     //����ʱ��ֹͣ
                R_State<=6'd0;
                R_SDA_I_O_SET<=1'b1;//Ĭ������Ϊ����ܽ�
                R_SDA_t<=1'b1;      //SDA���Ĭ������
                O_Done<=1'b0;
            end
        end
    end

    /*******������̬���ź�******/
    assign  IO_SDA=(R_SDA_I_O_SET)?R_SDA_t:1'bz;


endmodule
