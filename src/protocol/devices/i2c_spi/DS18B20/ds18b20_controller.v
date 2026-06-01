// DS18B20温度传感器控制器（持续读取模式）
// 版本: V2.2 - 参考第二版例程

module ds18b20_controller (
    input wire clk,
    input wire rst_n,
    input wire cmd_valid,
    input wire [7:0] cmd_code,
    input wire [7:0] cmd_payload,
    input wire cmd_payload_valid,
    input wire [15:0] payload_counter,
    output reg cmd_done,
    output reg [15:0] temp_data,
    output reg temp_valid,
    inout wire dq
);

localparam CMD_START_READ = 8'hA0;
localparam CMD_STOP_READ  = 8'hA2;

localparam IDLE = 4'b0000, SEND = 4'b0001, RECV = 4'b0010, SKIP = 4'b0011,
           CT   = 4'b0100, WAIT = 4'b0101, RC   = 4'b0110, RD   = 4'b0111;

reg [3:0] state_c, state_n;
reg skip_flag;

// 时序计数器
reg [6:0] cnt_2us;
reg [12:0] cnt_100us;
reg [14:0] cnt_480us;
reg [25:0] cnt_750ms;
reg [2:0] cnt_8bit;
reg [3:0] cnt_16bit;

wire add_cnt_2us, end_cnt_2us, add_cnt_100us, end_cnt_100us;
wire add_cnt_480us, end_cnt_480us, add_cnt_750ms, end_cnt_750ms;
wire add_cnt_8bit, end_cnt_8bit, add_cnt_16bit, end_cnt_16bit;

localparam TIME_2us = 100, TIME_10us = 500, TIME_100us = 5_000;
localparam TIME_480us = 24_000, TIME_750ms = 37_500_000;
localparam MAX_8bit = 8, MAX_16bit = 16;

// 单总线控制
wire dq_in;
reg dq_out, dq_en, idle_flag;
assign dq_in = dq;
assign dq = dq_en ? dq_out : 1'bz;

// 持续读取控制
reg read_enable, cmd_valid_d;
wire cmd_posedge;
assign cmd_posedge = cmd_valid && !cmd_valid_d;

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        cmd_valid_d <= 1'b0;
        cmd_done <= 1'b0;
        read_enable <= 1'b0;
    end else begin
        cmd_valid_d <= cmd_valid;
        cmd_done <= 1'b0;
        if (cmd_posedge) begin
            case (cmd_code)
                CMD_START_READ: begin read_enable <= 1'b1; cmd_done <= 1'b1; end
                CMD_STOP_READ:  begin read_enable <= 1'b0; cmd_done <= 1'b1; end
            endcase
        end
    end
end

// 2us计数器
always @(posedge clk or negedge rst_n) begin
    if (!rst_n) cnt_2us <= 7'd0;
    else if (add_cnt_2us) cnt_2us <= end_cnt_2us ? 7'd0 : cnt_2us + 7'd1;
end
assign add_cnt_2us = (state_c == IDLE);
assign end_cnt_2us = add_cnt_2us && (cnt_2us == TIME_2us - 1);

// 100us计数器
always @(posedge clk or negedge rst_n) begin
    if (!rst_n) cnt_100us <= 13'd0;
    else if (add_cnt_100us) cnt_100us <= end_cnt_100us ? 13'd0 : cnt_100us + 13'd1;
end
assign add_cnt_100us = (state_c == SKIP) || (state_c == CT) || (state_c == RC) || (state_c == RD);
assign end_cnt_100us = add_cnt_100us && (cnt_100us == TIME_100us - 1);

// 480us计数器
always @(posedge clk or negedge rst_n) begin
    if (!rst_n) cnt_480us <= 15'd0;
    else if (add_cnt_480us) cnt_480us <= end_cnt_480us ? 15'd0 : cnt_480us + 15'd1;
end
assign add_cnt_480us = (state_c == SEND) || (state_c == RECV);
assign end_cnt_480us = add_cnt_480us && (cnt_480us == TIME_480us - 1);

// 750ms计数器
always @(posedge clk or negedge rst_n) begin
    if (!rst_n) cnt_750ms <= 26'd0;
    else if (add_cnt_750ms) cnt_750ms <= end_cnt_750ms ? 26'd0 : cnt_750ms + 26'd1;
end
assign add_cnt_750ms = (state_c == WAIT);
assign end_cnt_750ms = add_cnt_750ms && (cnt_750ms == TIME_750ms - 1);

// 8bit计数器
always @(posedge clk or negedge rst_n) begin
    if (!rst_n) cnt_8bit <= 3'd0;
    else if (add_cnt_8bit) cnt_8bit <= end_cnt_8bit ? 3'd0 : cnt_8bit + 3'd1;
end
assign add_cnt_8bit = end_cnt_100us && ((state_c == CT) || (state_c == SKIP) || (state_c == RC));
assign end_cnt_8bit = add_cnt_8bit && (cnt_8bit == MAX_8bit - 1);

// 16bit计数器
always @(posedge clk or negedge rst_n) begin
    if (!rst_n) cnt_16bit <= 4'd0;
    else if (add_cnt_16bit) cnt_16bit <= end_cnt_16bit ? 4'd0 : cnt_16bit + 4'd1;
end
assign add_cnt_16bit = end_cnt_100us && (state_c == RD);
assign end_cnt_16bit = add_cnt_16bit && (cnt_16bit == MAX_16bit - 1);

// 存在脉冲检测
always @(posedge clk or negedge rst_n) begin
    if (!rst_n) idle_flag <= 1'b0;
    else if ((cnt_480us == 15'd5000) && (dq_in == 1'b0)) idle_flag <= 1'b1;
    else if (state_c == SKIP) idle_flag <= 1'b0;
end

// skip跳转标志
always @(posedge clk or negedge rst_n) begin
    if (!rst_n) skip_flag <= 1'b0;
    else if (state_c == WAIT && state_n == SEND) skip_flag <= 1'b1;
    else if (state_c == RD && end_cnt_16bit) skip_flag <= 1'b0;
end

// 主状态机一段
always @(posedge clk or negedge rst_n) begin
    if (!rst_n) state_c <= IDLE;
    else state_c <= state_n;
end

// 主状态机二段
wire IDLE_2_SEND = (state_c == IDLE) && (end_cnt_2us && read_enable);
wire SEND_2_RECV = (state_c == SEND) && end_cnt_480us;
wire RECV_2_SKIP = (state_c == RECV) && end_cnt_480us && idle_flag;
wire RECV_2_IDLE = (state_c == RECV) && end_cnt_480us && !idle_flag;
wire SKIP_2_CT   = (state_c == SKIP) && end_cnt_8bit && !skip_flag;
wire SKIP_2_RC   = (state_c == SKIP) && end_cnt_8bit && skip_flag;
wire CT_2_WAIT   = (state_c == CT) && end_cnt_8bit;
wire WAIT_2_SEND = (state_c == WAIT) && end_cnt_750ms;
wire RC_2_RD     = (state_c == RC) && end_cnt_8bit;
wire RD_2_SEND   = (state_c == RD) && end_cnt_16bit && read_enable;
wire RD_2_IDLE   = (state_c == RD) && end_cnt_16bit && !read_enable;

always @(*) begin
    case(state_c)
        IDLE: state_n = IDLE_2_SEND ? SEND : IDLE;
        SEND: state_n = SEND_2_RECV ? RECV : SEND;
        RECV: state_n = RECV_2_SKIP ? SKIP : (RECV_2_IDLE ? IDLE : RECV);
        SKIP: state_n = SKIP_2_CT ? CT : (SKIP_2_RC ? RC : SKIP);
        CT:   state_n = CT_2_WAIT ? WAIT : CT;
        WAIT: state_n = WAIT_2_SEND ? SEND : WAIT;
        RC:   state_n = RC_2_RD ? RD : RC;
        RD:   state_n = RD_2_SEND ? SEND : (RD_2_IDLE ? IDLE : RD);
        default: state_n = IDLE;
    endcase
end

// 主状态机三段
always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        dq_en <= 1'b0;
        dq_out <= 1'b0;
        temp_data <= 16'h0;
        temp_valid <= 1'b0;
    end else begin
        temp_valid <= 1'b0;
        case(state_c)
            IDLE: begin dq_en <= 1'b0; dq_out <= 1'b0; end
            SEND: begin dq_en <= 1'b1; dq_out <= 1'b0; end
            RECV: begin dq_en <= 1'b0; dq_out <= 1'b0; end
            SKIP: begin
                if (cnt_8bit == 3'd2 || cnt_8bit == 3'd3 || cnt_8bit == 3'd6 || cnt_8bit == 3'd7) begin
                    if (cnt_100us <= 100) begin dq_en <= 1'b1; dq_out <= 1'b0; end
                    else if (cnt_100us < 3100) begin dq_en <= 1'b1; dq_out <= 1'b1; end
                    else begin dq_en <= 1'b0; dq_out <= 1'b0; end
                end else begin
                    if (cnt_100us < 3100) begin dq_en <= 1'b1; dq_out <= 1'b0; end
                    else begin dq_en <= 1'b0; dq_out <= 1'b0; end
                end
            end
            CT: begin
                if (cnt_8bit == 3'd2 || cnt_8bit == 3'd6) begin
                    if (cnt_100us <= 100) begin dq_en <= 1'b1; dq_out <= 1'b0; end
                    else if (cnt_100us < 3100) begin dq_en <= 1'b1; dq_out <= 1'b1; end
                    else begin dq_en <= 1'b0; dq_out <= 1'b0; end
                end else begin
                    if (cnt_100us < 3100) begin dq_en <= 1'b1; dq_out <= 1'b0; end
                    else begin dq_en <= 1'b0; dq_out <= 1'b0; end
                end
            end
            WAIT: begin dq_en <= 1'b0; dq_out <= 1'b0; end
            RC: begin
                if (cnt_8bit == 3'd0 || cnt_8bit == 3'd6) begin
                    if (cnt_100us < 3100) begin dq_en <= 1'b1; dq_out <= 1'b0; end
                    else begin dq_en <= 1'b0; dq_out <= 1'b0; end
                end else begin
                    if (cnt_100us <= 100) begin dq_en <= 1'b1; dq_out <= 1'b0; end
                    else if (cnt_100us < 3100) begin dq_en <= 1'b1; dq_out <= 1'b1; end
                    else begin dq_en <= 1'b0; dq_out <= 1'b0; end
                end
            end
            RD: begin
                if (cnt_100us < 100) begin dq_en <= 1'b1; dq_out <= 1'b0; end
                else begin
                    dq_en <= 1'b0; dq_out <= 1'b0;
                    if (cnt_100us == TIME_10us) begin
                        temp_data[cnt_16bit] <= dq_in;
                        if (cnt_16bit == 4'd15) temp_valid <= 1'b1;
                    end
                end
            end
            default: begin dq_en <= 1'b0; dq_out <= 1'b0; end
        endcase
    end
end

endmodule
