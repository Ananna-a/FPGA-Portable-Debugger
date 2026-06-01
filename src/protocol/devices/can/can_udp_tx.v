//=============================================================================
// CAN总线UDP发送控制器
// 版本: V1.0
// 日期: 2025-11-28
//
// 功能:
//   - 将CAN接收数据通过以太网UDP发送到上位机
//   - 独立于ADC示波器的UDP通道
//   - 使用独立的UDP端口号避免冲突
//
// 端口分配:
//   - ADC示波器: UDP 6102 (ethernet_receiver.py)
//   - CAN总线:   UDP 6103 (独立端口)
//
// UDP包格式:
//   Header (16字节):
//     [0-1]:   0x5A 0xAA (帧头)
//     [2-5]:   序列号 (32位，小端)
//     [6-7]:   数据长度 (16位，小端)
//     [8-15]:  保留
//   Payload:
//     [0]:     帧类型 (0=标准帧, 1=扩展帧, 2=错误帧)
//     [1-4]:   CAN ID (32位，小端)
//     [5]:     DLC (数据长度 0-8)
//     [6-13]:  CAN数据 (最多8字节)
//=============================================================================

module can_udp_tx (
        input  wire        clk,           // 50MHz系统时钟
        input  wire        rst_n,

        // CAN接收数据接口（来自can_controller）
        input  wire        can_rx_valid,
        input  wire [7:0]  can_rx_data,
        input  wire        can_rx_last,
        input  wire [28:0] can_rx_id,
        input  wire        can_rx_ide,    // 0=标准帧, 1=扩展帧

        // UDP发送接口（连接到eth_udp_tx_wrapper）
        output reg         udp_tx_en,
        input  wire        udp_tx_done,
        output wire [15:0] udp_data_length,
        input  wire        udp_payload_req,
        output reg  [7:0]  udp_payload_data
    );

    //=============================================================================
    // CAN数据缓冲
    //=============================================================================
    reg [7:0] can_frame_buffer [0:31];  // 最大32字节缓冲
    reg [4:0] can_wr_ptr = 5'd0;
    reg [4:0] can_frame_len = 5'd0;
    reg can_collecting = 1'b0;
    reg udp_tx_done_sync = 1'b0;  // UDP发送完成标志（用于清零can_frame_len）

    // CAN帧收集
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            can_collecting <= 1'b0;
            can_wr_ptr <= 5'd0;
            can_frame_len <= 5'd0;
        end
        else begin
            // 响应UDP发送完成，清空缓冲区
            if (udp_tx_done_sync) begin
                can_frame_len <= 5'd0;
            end
            else if (can_rx_valid) begin
                if (!can_collecting) begin
                    // 开始新帧，填充元数据
                    can_collecting <= 1'b1;
                    can_wr_ptr <= 5'd0;

                    // [0]: 帧类型
                    can_frame_buffer[0] <= can_rx_ide ? 8'd1 : 8'd0;

                    // [1-4]: CAN ID (32位小端)
                    can_frame_buffer[1] <= can_rx_id[7:0];
                    can_frame_buffer[2] <= can_rx_id[15:8];
                    can_frame_buffer[3] <= can_rx_id[23:16];
                    can_frame_buffer[4] <= {3'b0, can_rx_id[28:24]};

                    // [5]: DLC占位
                    can_frame_buffer[5] <= 8'd0;

                    can_wr_ptr <= 5'd6;  // 从数据区开始
                end
                else begin
                    // 收集数据
                    can_frame_buffer[can_wr_ptr] <= can_rx_data;
                    can_wr_ptr <= can_wr_ptr + 5'd1;
                end

                // 最后一个字节
                if (can_rx_last) begin
                    can_collecting <= 1'b0;
                    can_frame_len <= can_wr_ptr + 5'd1;
                    // 更新DLC字段
                    can_frame_buffer[5] <= (can_wr_ptr + 5'd1) - 5'd6;
                end
            end
        end
    end

    //=============================================================================
    // UDP发送状态机
    //=============================================================================
    localparam UDP_IDLE       = 3'd0;
    localparam UDP_SEND_FRAME = 3'd1;
    localparam UDP_WAIT_DONE  = 3'd2;

    reg [2:0] udp_state = UDP_IDLE;
    reg [31:0] udp_seq_num = 32'd0;  // UDP序列号
    reg [4:0] udp_byte_cnt = 5'd0;

    // UDP包总长度 = Header(16) + CAN帧数据
    assign udp_data_length = 16'd16 + {11'd0, can_frame_len};

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            udp_state <= UDP_IDLE;
            udp_tx_en <= 1'b0;
            udp_payload_data <= 8'h0;
            udp_byte_cnt <= 5'd0;
            udp_seq_num <= 32'd0;
            udp_tx_done_sync <= 1'b0;
        end
        else begin
            udp_tx_done_sync <= 1'b0;  // 默认清零（单周期脉冲）

            case (udp_state)
                UDP_IDLE: begin
                    udp_tx_en <= 1'b0;

                    // 有完整CAN帧等待发送
                    if (!can_collecting && can_frame_len > 0) begin
                        udp_tx_en <= 1'b1;  // 触发UDP发送
                        udp_byte_cnt <= 5'd0;
                        udp_state <= UDP_SEND_FRAME;
                    end
                end

                UDP_SEND_FRAME: begin
                    udp_tx_en <= 1'b0;

                    // UDP模块请求数据
                    if (udp_payload_req) begin
                        if (udp_byte_cnt < 16) begin
                            // 发送UDP Header
                            case (udp_byte_cnt)
                                5'd0:
                                    udp_payload_data <= 8'h5A;  // 帧头
                                5'd1:
                                    udp_payload_data <= 8'hAA;
                                5'd2:
                                    udp_payload_data <= udp_seq_num[7:0];     // 序列号
                                5'd3:
                                    udp_payload_data <= udp_seq_num[15:8];
                                5'd4:
                                    udp_payload_data <= udp_seq_num[23:16];
                                5'd5:
                                    udp_payload_data <= udp_seq_num[31:24];
                                5'd6:
                                    udp_payload_data <= {3'd0, can_frame_len};   // 数据长度 (低字节)
                                5'd7:
                                    udp_payload_data <= 8'h00;                   // 数据长度高字节（固定0，最大32字节）
                                default:
                                    udp_payload_data <= 8'h00;  // 保留字节
                            endcase
                        end
                        else begin
                            // 发送CAN帧数据
                            udp_payload_data <= can_frame_buffer[udp_byte_cnt - 5'd16];
                        end

                        udp_byte_cnt <= udp_byte_cnt + 5'd1;

                        // 发送完成
                        if (udp_byte_cnt >= (16 + can_frame_len - 1)) begin
                            udp_state <= UDP_WAIT_DONE;
                        end
                    end
                end

                UDP_WAIT_DONE: begin
                    if (udp_tx_done) begin
                        udp_seq_num <= udp_seq_num + 32'd1;
                        udp_tx_done_sync <= 1'b1;  // 发出清空信号
                        udp_state <= UDP_IDLE;
                    end
                end

                default:
                    udp_state <= UDP_IDLE;
            endcase
        end
    end

endmodule
