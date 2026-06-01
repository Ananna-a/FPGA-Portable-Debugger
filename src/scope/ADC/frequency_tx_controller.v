//=============================================================================
// 频率数据发送控制器 V2.0 - 双通道版本
// 功能：将CH1和CH2频率测量结果打包并通过CH340串口发送给上位机
// 协议：发送8字节频率数据（小端序，CH1的4字节 + CH2的4字节）
// 通道：使用类似SPI/I2C的独立数据通道（通道ID=0x03）
// 日期：2025-11-20
//=============================================================================

module frequency_tx_controller(
        input  wire         clk,            // 系统时钟（50MHz）
        input  wire         rst_n,          // 复位信号，低电平有效

        // 频率数据输入（来自frequency_counter）
        input  wire [31:0]  frequency_ch1,  // CH1频率值(Hz)
        input  wire [31:0]  frequency_ch2,  // CH2频率值(Hz)
        input  wire         freq_valid_ch1, // CH1频率有效标志
        input  wire         freq_valid_ch2, // CH2频率有效标志
        input  wire         freq_request,   // 频率请求命令（来自命令解析器0x27）

        // CH340串口发送接口（连接到UART MUX）
        output reg  [7:0]   tx_data,        // 发送数据
        output reg          tx_send_en,     // 发送使能
        input  wire         tx_done,        // 发送完成标志
        input  wire         tx_busy,        // 发送忙标志

        // 状态输出
        output reg          sending,        // 发送中标志
        output reg  [2:0]   tx_state_debug, // 状态机调试输出

        // 新增：LED调试输出
        output reg          led_wait_valid, // 等待freq_valid的状态
        output reg          led_sending     // 正在发送数据
    );

    //=========================================================================
    // 状态机定义（V2.0：扩展到4位支持8字节发送）
    //=========================================================================
    localparam IDLE         = 4'd0;  // 空闲
    localparam WAIT_VALID   = 4'd1;  // 等待freq_valid
    localparam SEND_CH1_B0  = 4'd2;  // 发送CH1字节0（最低字节）
    localparam SEND_CH1_B1  = 4'd3;  // 发送CH1字节1
    localparam SEND_CH1_B2  = 4'd4;  // 发送CH1字节2
    localparam SEND_CH1_B3  = 4'd5;  // 发送CH1字节3
    localparam SEND_CH2_B0  = 4'd6;  // 发送CH2字节0
    localparam SEND_CH2_B1  = 4'd7;  // 发送CH2字节1
    localparam SEND_CH2_B2  = 4'd8;  // 发送CH2字节2
    localparam SEND_CH2_B3  = 4'd9;  // 发送CH2字节3
    localparam WAIT_DONE    = 4'd10; // 等待发送完成
    localparam COMPLETE     = 4'd11; // 完成

    reg [3:0] state;
    reg [31:0] freq_ch1_reg;  // CH1频率值寄存器（锁存）
    reg [31:0] freq_ch2_reg;  // CH2频率值寄存器（锁存）

    //=========================================================================
    // 🔥 新增：超时保护（防止永久阻塞在WAIT_VALID状态）
    //=========================================================================
    localparam TIMEOUT_CYCLES = 32'd150_000_000;  // 3秒超时 @ 50MHz
    reg [31:0] timeout_counter;  // 超时计数器

    //=========================================================================
    // 频率请求边沿检测
    //=========================================================================
    reg freq_request_d1;
    wire freq_request_posedge;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            freq_request_d1 <= 1'b0;
        else
            freq_request_d1 <= freq_request;
    end

    assign freq_request_posedge = freq_request && (!freq_request_d1);

    //=========================================================================
    // 主状态机
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            tx_data <= 8'd0;
            tx_send_en <= 1'b0;
            freq_ch1_reg <= 32'd0;
            freq_ch2_reg <= 32'd0;
            sending <= 1'b0;
            tx_state_debug <= 3'd0;
            led_wait_valid <= 1'b0;
            led_sending <= 1'b0;
            timeout_counter <= 32'd0;  // 🔥 复位超时计数器
        end
        else begin
            case (state)
                //-------------------------------------------------------------
                // IDLE: 等待频率请求命令
                //-------------------------------------------------------------
                IDLE: begin
                    tx_send_en <= 1'b0;
                    sending <= 1'b0;
                    led_wait_valid <= 1'b0;
                    led_sending <= 1'b0;
                    timeout_counter <= 32'd0;  // 🔥 清零超时计数器

                    // 收到频率请求后，进入等待状态
                    if (freq_request_posedge) begin
                        state <= WAIT_VALID;
                        sending <= 1'b1;
                        led_wait_valid <= 1'b1;  // 🔴 点亮LED表示等待freq_valid
                        timeout_counter <= 32'd0;  // 🔥 开始计时
                    end
                end

                //-------------------------------------------------------------
                // WAIT_VALID: 等待双通道频率有效信号（带超时保护）
                //-------------------------------------------------------------
                WAIT_VALID: begin
                    led_wait_valid <= 1'b1;  // 保持亮

                    // 🔥 超时检测：3秒内未收到freq_valid，自动返回IDLE
                    if (timeout_counter >= TIMEOUT_CYCLES) begin
                        state <= IDLE;
                        sending <= 1'b0;
                        led_wait_valid <= 1'b0;
                        timeout_counter <= 32'd0;
                    end
                    // 正常流程：收到两个通道的valid信号
                    else if (freq_valid_ch1 && freq_valid_ch2) begin
                        freq_ch1_reg <= frequency_ch1;  // 锁存CH1频率值
                        freq_ch2_reg <= frequency_ch2;  // 锁存CH2频率值
                        state <= SEND_CH1_B0;
                        led_wait_valid <= 1'b0;
                        led_sending <= 1'b1;  // 🟢 开始发送数据
                        timeout_counter <= 32'd0;  // 🔥 清零计数器
                    end
                    // 继续等待，累加超时计数器
                    else begin
                        timeout_counter <= timeout_counter + 32'd1;
                    end
                end

                //-------------------------------------------------------------
                // CH1频率发送（4字节）
                //-------------------------------------------------------------
                SEND_CH1_B0: begin
                    led_sending <= 1'b1;
                    if (!tx_busy) begin
                        tx_data <= freq_ch1_reg[7:0];
                        tx_send_en <= 1'b1;
                        state <= WAIT_DONE;
                        tx_state_debug <= 3'd0;
                    end
                end

                SEND_CH1_B1: begin
                    led_sending <= 1'b1;
                    if (!tx_busy) begin
                        tx_data <= freq_ch1_reg[15:8];
                        tx_send_en <= 1'b1;
                        state <= WAIT_DONE;
                        tx_state_debug <= 3'd1;
                    end
                end

                SEND_CH1_B2: begin
                    led_sending <= 1'b1;
                    if (!tx_busy) begin
                        tx_data <= freq_ch1_reg[23:16];
                        tx_send_en <= 1'b1;
                        state <= WAIT_DONE;
                        tx_state_debug <= 3'd2;
                    end
                end

                SEND_CH1_B3: begin
                    led_sending <= 1'b1;
                    if (!tx_busy) begin
                        tx_data <= freq_ch1_reg[31:24];
                        tx_send_en <= 1'b1;
                        state <= WAIT_DONE;
                        tx_state_debug <= 3'd3;
                    end
                end

                //-------------------------------------------------------------
                // CH2频率发送（4字节）
                //-------------------------------------------------------------
                SEND_CH2_B0: begin
                    led_sending <= 1'b1;
                    if (!tx_busy) begin
                        tx_data <= freq_ch2_reg[7:0];
                        tx_send_en <= 1'b1;
                        state <= WAIT_DONE;
                        tx_state_debug <= 3'd4;
                    end
                end

                SEND_CH2_B1: begin
                    led_sending <= 1'b1;
                    if (!tx_busy) begin
                        tx_data <= freq_ch2_reg[15:8];
                        tx_send_en <= 1'b1;
                        state <= WAIT_DONE;
                        tx_state_debug <= 3'd5;
                    end
                end

                SEND_CH2_B2: begin
                    led_sending <= 1'b1;
                    if (!tx_busy) begin
                        tx_data <= freq_ch2_reg[23:16];
                        tx_send_en <= 1'b1;
                        state <= WAIT_DONE;
                        tx_state_debug <= 3'd6;
                    end
                end

                SEND_CH2_B3: begin
                    led_sending <= 1'b1;
                    if (!tx_busy) begin
                        tx_data <= freq_ch2_reg[31:24];
                        tx_send_en <= 1'b1;
                        state <= WAIT_DONE;
                        tx_state_debug <= 3'd7;
                    end
                end

                //-------------------------------------------------------------
                // WAIT_DONE: 等待字节发送完成
                //-------------------------------------------------------------
                WAIT_DONE: begin
                    led_sending <= 1'b1;
                    tx_send_en <= 1'b0;  // 清除发送使能

                    if (tx_done) begin
                        // 根据当前调试状态决定下一个字节
                        case (tx_state_debug)
                            3'd0:
                                state <= SEND_CH1_B1;
                            3'd1:
                                state <= SEND_CH1_B2;
                            3'd2:
                                state <= SEND_CH1_B3;
                            3'd3:
                                state <= SEND_CH2_B0;  // CH1完成，开始CH2
                            3'd4:
                                state <= SEND_CH2_B1;
                            3'd5:
                                state <= SEND_CH2_B2;
                            3'd6:
                                state <= SEND_CH2_B3;
                            3'd7:
                                state <= COMPLETE;     // CH2完成
                            default:
                                state <= COMPLETE;
                        endcase
                    end
                end

                //-------------------------------------------------------------
                // COMPLETE: 发送完成，返回IDLE
                //-------------------------------------------------------------
                COMPLETE: begin
                    sending <= 1'b0;
                    tx_send_en <= 1'b0;
                    led_sending <= 1'b0;  // 🔴 熄灭发送LED
                    state <= IDLE;
                end

                default: begin
                    state <= IDLE;
                    led_wait_valid <= 1'b0;
                    led_sending <= 1'b0;
                end
            endcase
        end
    end

    //=========================================================================
    // 使用说明（V2.1双通道版本 + 超时保护）：
    //
    // 1. 数据格式：
    //    - 发送8字节，小端序（低字节在前）
    //    - 格式：[CH1_B0, CH1_B1, CH1_B2, CH1_B3, CH2_B0, CH2_B1, CH2_B2, CH2_B3]
    //    - 例如：CH1=10kHz, CH2=100kHz
    //      -> 0x10 0x27 0x00 0x00 0xA0 0x86 0x01 0x00
    //
    // 2. 通道分配：
    //    - 频率数据使用独立通道（通道ID=0x03，在UART MUX中配置）
    //    - 不与命令应答帧混淆
    //
    // 3. 超时保护（V2.1新增）：
    //    - WAIT_VALID状态增加3秒超时检测（150,000,000个时钟周期 @ 50MHz）
    //    - 如果频率计数器未在3秒内返回freq_valid信号，自动返回IDLE状态
    //    - 防止与其他功能（如数字信号测量）发生冲突导致永久阻塞
    //    - 超时后不会发送任何数据，上位机会收到"频率测量超时"提示
    //
    // 4. 时序：
    //    - 上位机发送0x27命令
    //    - FPGA收到命令，发送应答帧
    //    - FPGA拉高freq_request，启动频率测量
    //    - 本模块等待freq_valid_ch1 && freq_valid_ch2（最多3秒，超时则放弃）
    //    - 连续发送8字节频率值（CH1前CH2后）
    //    - 完成后返回IDLE
    //
    // 5. 上位机接收：
    //    - 先收到7字节应答帧
    //    - 再收到8字节连续数据（可能在同一包或分两包）
    //    - 解析：
    //      ch1_freq = byte0 + (byte1<<8) + (byte2<<16) + (byte3<<24)
    //      ch2_freq = byte4 + (byte5<<8) + (byte6<<16) + (byte7<<24)
    //
    // 6. V2.0更新：
    //    - 支持双通道频率同时发送
    //    - 等待两个通道的freq_valid都有效后再发送
    //    - 状态机扩展到4位支持11个状态
    //
    // 7. V2.1更新（2025-11-27）：
    //    - 新增3秒超时保护，解决数字信号测量后ADC测频阻塞问题
    //    - 添加timeout_counter计数器（32位，支持150M周期）
    //    - WAIT_VALID状态会在超时后自动返回IDLE，不影响后续命令
    //=========================================================================

endmodule

