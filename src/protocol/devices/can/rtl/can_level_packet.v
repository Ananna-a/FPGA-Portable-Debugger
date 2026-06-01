
//--------------------------------------------------------------------------------------------------------
// Module  : can_level_packet
// Type    : synthesizable, IP's sub module
// Standard: Verilog 2001 (IEEE1364-2001)
// Function: CAN bus packet level controller,
//           instantiated by can_top
//--------------------------------------------------------------------------------------------------------

module can_level_packet #(
        parameter [ 0:0] TX_RTR         = 1'b0,
        parameter [10:0] TX_ID          = 11'h456,
        parameter [15:0] default_c_PTS  = 16'd34,
        parameter [15:0] default_c_PBS1 = 16'd5,
        parameter [15:0] default_c_PBS2 = 16'd10

    ) (
        input  wire        rstn,      // set to 1 while working
        input  wire        clk,       // system clock

        // CAN TX and RX
        input  wire        can_rx,
        output wire        can_tx,

        // user tx packet interface
        input  wire        tx_start,
        input  wire [28:0] tx_id,     // 🔥 V2.8: 29位ID (支持标准帧和扩展帧)
        input  wire        tx_ide,    // 🔥 V2.8: IDE标志 (0=标准帧, 1=扩展帧)
        input  wire [ 3:0] tx_len,    // 发送数据长度 (DLC: 0-8字节)
        input  wire [63:0] tx_data,   // 扩展到64位支持8字节
        output reg         tx_done,
        output reg         tx_acked,

        // user rx packet interface
        output reg         rx_valid,
        output reg  [28:0] rx_id,
        output reg         rx_ide,
        output reg         rx_rtr,
        output reg  [ 3:0] rx_len,
        output reg  [63:0] rx_data,
        input  wire        rx_ack
    );



    initial
        {tx_done, tx_acked} = 1'b0;
    initial
        {rx_valid,rx_id,rx_ide,rx_rtr,rx_len,rx_data} = 0;


    function  [14:0] crc15;
        input [14:0] crc_val;
        input [ 0:0] in_bit;
        begin
            crc15 = ( {crc_val[13:0], 1'b0} ^ ((crc_val[14]^in_bit) ? 15'h4599 : 15'h0) );
        end
    endfunction


    wire bit_req;
    wire bit_rx;
    reg  bit_tx = 1'b1;

    can_level_bit #(
                      .default_c_PTS   ( default_c_PTS    ),
                      .default_c_PBS1  ( default_c_PBS1   ),
                      .default_c_PBS2  ( default_c_PBS2   )
                  ) u_can_level_bit (
                      .rstn            ( rstn             ),
                      .clk             ( clk              ),
                      .can_rx          ( can_rx           ),
                      .can_tx          ( can_tx           ),
                      .req             ( bit_req          ),
                      .rbit            ( bit_rx           ),
                      .tbit            ( bit_tx           )
                  );


    reg [ 7:0] rx_history = 8'd0;
    reg [ 3:0] tx_history = 4'hF;
    wire       rx_end = (rx_history == 8'hFF);
    wire       rx_err = (rx_history[5:0] == 6'd0);
    wire       rx_ben = (rx_history[4:0] != 5'd0) && (rx_history[4:0] != 5'd31);
    wire       tx_ben = ({tx_history,bit_tx} != 5'd0) && ({tx_history,bit_tx} != 5'd31);

    always @ (posedge clk or negedge rstn)
        if(~rstn) begin
            rx_history <= 8'd0;
            tx_history <= 4'hF;
        end
        else begin
            if(bit_req) begin
                rx_history <= {rx_history[6:0], bit_rx};
                tx_history <= {tx_history[2:0], bit_tx};
            end
        end



    reg        tx_arbitrary = 1'b0;

    reg [14:0] rx_crc = 15'd0;
    wire[14:0] rx_crc_next = {rx_crc[13:0], 1'b0} ^ (rx_crc[14] ^ bit_rx ? 15'h4599 : 15'h0);

    reg [81:0] tx_shift = 82'h3_ffff_ffff_ffff_ffff_ffff;  // 标准帧用82位
    reg [19:0] tx_ext_id = 20'h0;  // 🔥 V2.8: 存储扩展帧的18位扩展ID
    reg        tx_ide_latch = 1'b0;  // 🔥 V2.8: 锁存IDE标志
    reg [14:0] tx_crc = 15'd0;
    wire[14:0] tx_crc_next = {tx_crc[13:0], 1'b0} ^ (tx_crc[14] ^ tx_shift[81] ? 15'h4599 : 15'h0);

    wire[ 3:0] rx_len_next = {rx_len[2:0], bit_rx};
    wire[ 7:0] rx_cnt = rx_len[3] ? 8'd63 : {1'd0, rx_len, 3'd0} - 8'd1;

    localparam [4:0] INIT         = 5'd0,
               IDLE         = 5'd1,
               TX_ID_MSB    = 5'd2,
               TRX_ID_BASE  = 5'd3,
               TX_ID_EXT    = 5'd4,   // 🔥 V2.8: 扩展帧ID发送
               TX_PAYLOAD   = 5'd5,
               TX_ACK_DEL   = 5'd6,
               TX_ACK       = 5'd7,
               TX_EOF       = 5'd8,
               RX_IDE_BIT   = 5'd9,
               RX_ID_EXTEND = 5'd10,
               RX_RESV1_BIT = 5'd11,
               RX_CTRL      = 5'd12,
               RX_DATA      = 5'd13,
               RX_CRC       = 5'd14,
               RX_ACK       = 5'd15,
               RX_EOF       = 5'd16;

    reg [ 7:0] cnt = 8'd0;
    reg [ 4:0] stat = INIT;  // 🔥 V2.8: 扩展到5位

    reg rx_valid_pre = 1'b0;
    reg rx_valid_latch = 1'b0;
    reg rx_ack_latch = 1'b0;

    always @ (posedge clk or negedge rstn)
        if(~rstn) begin
            rx_valid <= 1'b0;
            rx_valid_latch <= 1'b0;
            rx_ack_latch <= 1'b0;
        end
        else begin
            rx_valid <= rx_valid_pre & (rx_crc==15'd0);
            rx_valid_latch <= rx_valid;
            if (rx_valid_latch)
                rx_ack_latch <= rx_ack;
        end

    always @ (posedge clk or negedge rstn)
        if(~rstn) begin
            {tx_done, tx_acked} <= 1'b0;
            rx_valid_pre <= 1'b0;
            {rx_id,rx_ide,rx_rtr,rx_len,rx_data,rx_crc} <= 0;
            bit_tx <= 1'b1;
            tx_arbitrary <= 1'b0;
            tx_crc <= 15'd0;
            tx_shift <= 82'h3_ffff_ffff_ffff_ffff_ffff;
            tx_ext_id <= 20'h0;       // 🔥 V2.8
            tx_ide_latch <= 1'b0;     // 🔥 V2.8
            cnt <= 8'd0;
            stat <= INIT;
        end
        else begin
            {tx_done, tx_acked} <= 1'b0;
            rx_valid_pre <= 1'b0;

            if(bit_req) begin
                bit_tx <= 1'b1;

                case(stat)
                    INIT :
                        if(rx_end)
                            stat <= IDLE;

                    IDLE : begin
                        tx_arbitrary <= 1'b0;
                        {rx_id,rx_ide,rx_rtr,rx_len,rx_data,rx_crc} <= 0;
                        tx_crc <= 15'd0;
                        // 🔥 V2.8: 根据IDE标志组装不同格式
                        if (tx_ide) begin
                            // 扩展帧: 取29位ID的高11位作为基础ID, SRR=1(替代RTR), IDE=1
                            tx_shift <= {tx_id[28:18], 1'b1, 1'b1, 1'b0, tx_len, tx_data};  // SRR=1, IDE=1, r0=0
                            tx_ext_id <= {tx_id[17:0], TX_RTR, 1'b0};  // 18位扩展ID + RTR + r1
                            tx_ide_latch <= 1'b1;
                        end
                        else begin
                            // 标准帧: 11位ID + RTR + IDE=0 + r0
                            tx_shift <= {tx_id[10:0], TX_RTR, 1'b0, 1'b0, tx_len, tx_data};
                            tx_ide_latch <= 1'b0;
                        end
                        if(bit_rx == 1'b0) begin
                            cnt <= 8'd0;
                            stat <= TRX_ID_BASE;
                        end
                        else if(cnt<8'd20) begin
                            cnt <= cnt + 8'd1;
                        end
                        else if(tx_start) begin
                            bit_tx <= 1'b0;
                            cnt <= 8'd0;
                            stat <= TX_ID_MSB;
                        end
                    end

                    TX_ID_MSB :
                        if(bit_rx) begin
                            stat <= TX_EOF;
                        end
                        else begin
                            {bit_tx, tx_shift} <= {tx_shift, 1'b1};
                            tx_crc <= tx_crc_next;
                            tx_arbitrary <= 1'b1;
                            stat <= TRX_ID_BASE;
                        end

                    TRX_ID_BASE : begin
                        if(tx_arbitrary && bit_rx==bit_tx) begin
                            if(tx_ben) begin
                                {bit_tx, tx_shift} <= {tx_shift, 1'b1};
                                tx_crc <= tx_crc_next;
                            end
                            else begin
                                bit_tx <= ~tx_history[0];
                            end
                        end
                        else begin
                            tx_arbitrary <= 1'b0;
                        end
                        if(rx_end) begin
                            stat <= IDLE;
                        end
                        else if(rx_err) begin
                            stat <= RX_EOF;
                        end
                        else if(rx_ben) begin
                            rx_crc <= rx_crc_next;
                            cnt <= cnt + 8'd1;
                            if(cnt<8'd11) begin
                                rx_id <= {rx_id[27:0], bit_rx};
                            end
                            else begin
                                rx_rtr <= bit_rx;
                                if( !(tx_arbitrary && bit_rx==bit_tx) ) begin              // TX arbitrary failed
                                    cnt <= 8'd0;
                                    stat <= RX_IDE_BIT;
                                end
                                else if(tx_ben) begin
                                    // 🔥 V2.8: 发送端检查是否需要发送扩展ID
                                    if (tx_ide_latch) begin
                                        cnt <= 8'd0;
                                        stat <= TX_ID_EXT;  // 转到扩展ID发送
                                    end
                                    else begin
                                        cnt <= 8'd0;
                                        stat <= TX_PAYLOAD;  // 标准帧直接到payload
                                    end
                                end
                            end
                        end
                        else if(cnt>8'd11) begin
                            // 🔥 V2.8: 发送端超时也检查扩展帧
                            if (tx_ide_latch) begin
                                cnt <= 8'd0;
                                stat <= TX_ID_EXT;
                            end
                            else begin
                                cnt <= 8'd0;
                                stat <= TX_PAYLOAD;
                            end
                        end
                    end

                    // 🔥 V2.8: 新增扩展ID发送状态 (发送18位扩展ID + RTR + r1)
                    TX_ID_EXT : begin
                        if(bit_rx != bit_tx) begin
                            stat <= TX_EOF;  // 冲突，中止发送
                        end
                        else if(tx_ben) begin
                            {bit_tx, tx_ext_id} <= {tx_ext_id, 1'b1};
                            tx_crc <= crc15(tx_crc, tx_ext_id[19]);  // 更新CRC
                            cnt <= cnt + 8'd1;
                            if(cnt >= 8'd19) begin  // 发送完20位(18位扩展ID + RTR + r1)
                                cnt <= 8'd0;
                                stat <= TX_PAYLOAD;
                            end
                        end
                    end

                    TX_PAYLOAD :
                        if(bit_rx != bit_tx) begin
                            stat <= TX_EOF;
                        end
                        else if(tx_ben) begin
                            // 动态计算CRC插入位置和结束位置（基于tx_len）
                            // CRC位置 = 4(DLC) + tx_len*8(数据位) = 4 + tx_len*8
                            // 总位数 = 4 + tx_len*8 + 15(CRC) + 1(CRC_DEL) = 20 + tx_len*8

                            // 🔧 修复: 在CRC插入周期，必须分开处理避免多重驱动冲突
                            if(cnt==(4'd4 + {tx_len[3:0], 3'd0})) begin
                                // CRC插入时刻：先发送最后一个数据位，然后填充CRC到移位寄存器
                                bit_tx <= tx_shift[81];                    // 发送当前位
                                tx_shift <= {tx_crc_next, {67{1'b1}}};     // 用CRC填充高15位，低位填1
                                tx_crc <= tx_crc_next;                     // 锁定CRC值
                            end
                            else begin
                                // 正常移位发送
                                {bit_tx, tx_shift} <= {tx_shift, 1'b1};
                                tx_crc <= tx_crc_next;
                            end

                            if(cnt<(8'd20 + {tx_len[3:0], 3'd0})) begin  // 动态结束位置
                                cnt <= cnt + 8'd1;
                            end
                            else begin
                                cnt <= 8'd0;
                                stat <= TX_ACK_DEL;
                            end
                        end
                        else begin
                            bit_tx <= ~tx_history[0];
                        end
                    TX_ACK_DEL :
                        stat <= bit_rx ? TX_ACK : TX_EOF;

                    TX_ACK : begin
                        tx_done <= 1'b1;
                        tx_acked <= ~bit_rx;
                        stat <= TX_EOF;
                    end

                    TX_EOF :
                        if(cnt<8'd8) begin
                            cnt <= cnt + 8'd1;
                        end
                        else begin
                            cnt <= 8'd0;
                            stat <= RX_EOF;
                        end

                    RX_IDE_BIT :
                        if(rx_end) begin
                            stat <= IDLE;
                        end
                        else if(rx_err) begin
                            stat <= RX_EOF;
                        end
                        else if(rx_ben) begin
                            rx_crc <= rx_crc_next;
                            rx_ide <= bit_rx;
                            stat <= bit_rx ? RX_ID_EXTEND : RX_CTRL;
                        end

                    RX_ID_EXTEND :
                        if(rx_end) begin
                            stat <= IDLE;
                        end
                        else if(rx_err) begin
                            stat <= RX_EOF;
                        end
                        else if(rx_ben) begin
                            rx_crc <= rx_crc_next;
                            if(cnt<8'd18) begin
                                rx_id <= {rx_id[27:0], bit_rx};
                                cnt <= cnt + 8'd1;
                            end
                            else begin
                                rx_rtr <= bit_rx;
                                cnt <= 8'd0;
                                stat <= RX_RESV1_BIT;
                            end
                        end

                    RX_RESV1_BIT :
                        if(rx_end) begin
                            stat <= IDLE;
                        end
                        else if(rx_err) begin
                            stat <= RX_EOF;
                        end
                        else if(rx_ben) begin
                            rx_crc <= rx_crc_next;
                            stat <= bit_rx ? RX_EOF : RX_CTRL;
                        end

                    RX_CTRL :
                        if(rx_end) begin
                            stat <= IDLE;
                        end
                        else if(rx_err) begin
                            stat <= RX_EOF;
                        end
                        else if(rx_ben) begin
                            rx_crc <= rx_crc_next;
                            rx_len <= rx_len_next;
                            if(cnt<8'd4) begin
                                cnt <= cnt + 8'd1;
                            end
                            else begin
                                cnt <= 8'd0;
                                stat <= (rx_len_next!=4'd0 && rx_rtr==1'b0) ? RX_DATA : RX_CRC;
                            end
                        end

                    RX_DATA :
                        if(rx_end) begin
                            stat <= IDLE;
                        end
                        else if(rx_err) begin
                            stat <= RX_EOF;
                        end
                        else if(rx_ben) begin
                            rx_crc <= rx_crc_next;
                            rx_data <= {rx_data[62:0], bit_rx};
                            if(cnt<rx_cnt) begin
                                cnt <= cnt + 8'd1;
                            end
                            else begin
                                cnt <= 8'd0;
                                stat <= RX_CRC;
                            end
                        end

                    RX_CRC :
                        if(rx_end) begin
                            stat <= IDLE;
                        end
                        else if(rx_err) begin
                            stat <= RX_EOF;
                        end
                        else if(rx_ben) begin
                            rx_crc <= rx_crc_next;
                            if(cnt<8'd14) begin
                                cnt <= cnt + 8'd1;
                            end
                            else begin
                                cnt <= 8'd0;
                                stat <= RX_ACK;
                                rx_valid_pre <= 1'b1;
                            end
                        end

                    RX_ACK :
                        if(rx_end) begin
                            stat <= IDLE;
                        end
                        else if(rx_err) begin
                            stat <= RX_EOF;
                        end
                        else if(rx_ben) begin
                            if(bit_rx && rx_crc==15'd0 && rx_ack_latch) // send ACK=0 bit if DEL=1 and no CRC error and user permission
                                bit_tx <= 1'b0;                         // send ACK
                            stat <= RX_EOF;
                        end

                    default : // RX_EOF :
                        if(rx_end)
                            stat <= IDLE;

                endcase
            end
        end



endmodule
