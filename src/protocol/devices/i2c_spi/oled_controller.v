/****************************************************************************
 * OLED控制器 - 完全基于参考例程架构（Oled_Top.v）
 * 
 * 功能：接收上位机命令，使用参考例程的完整OLED模块
 * 
 * 命令：
 *   - 0x73: OLED初始化
 *   - 0x74: OLED清屏
 *   - 0x75: OLED全亮显示
 * 
 * 架构：完全复制Oled_Top.v的状态机和模块实例化
 * 
 * 作者: AI Assistant  
 * 日期: 2025-10-31
 ****************************************************************************/

module oled_controller (
        input wire clk,                    // 系统时钟 50MHz
        input wire rst,                    // 复位信号（低有效）

        // 命令接口
        input wire cmd_valid,              // 命令有效
        input wire [7:0] cmd_code,         // 命令码
        output reg cmd_done,               // 命令完成

        // I2C物理接口
        output wire i2c_scl,               // SCL时钟线
        inout wire i2c_sda                 // SDA数据线
    );

    // 命令码定义
    localparam CMD_OLED_INIT  = 8'h73;  // OLED初始化
    localparam CMD_OLED_CLEAR = 8'h74;  // OLED清屏
    localparam CMD_OLED_ON    = 8'h75;  // OLED全亮显示
    localparam CMD_OLED_SHOW  = 8'h76;  // OLED显示文本

    localparam RST_T = 1'b0;  // 复位有效电平

    // 状态机（简化版Oled_Top.v）
    localparam Oled_Idle   = 3'd0;  // 空闲
    localparam Oled_Init   = 3'd1;  // 初始化
    localparam Oled_Clear  = 3'd2;  // 清屏
    localparam Oled_On     = 3'd3;  // 全亮显示
    localparam Oled_Show   = 3'd4;  // 显示文本

    reg [2:0] state, next_state;
    reg [31:0] delay_time;  // 上电延时

    // OLED模块接口
    wire init_req;
    wire init_finish;
    wire [23:0] init_data;

    wire clear_req;
    wire clear_ack;
    wire [23:0] clear_data;

    wire On_req;  // 注意：参考例程使用大写On_req
    wire On_ack;
    wire [23:0] On_data;

    wire show_req;   // 显示文本请求
    wire show_end;   // 显示完成
    wire [23:0] show_data;
    wire [6:0] start_x;
    wire [3:0] start_y;
    wire [5:0] show_select;
    wire show_ack;

    // I2C接口
    wire iic_req;
    wire w_ack;  // 参考例程使用w_ack而不是iic_done
    reg [23:0] iic_data;

    // 请求信号生成（修复：避免重复触发）
    // 关键修复：只在进入状态的第一个周期产生请求
    reg init_req_reg;
    reg clear_req_reg;
    reg on_req_reg;
    reg show_req_reg;
    reg [2:0] state_d;  // 延迟一拍用于边沿检测

    // 状态切换检测
    wire state_changed = (state != state_d);
    wire enter_init = (state == Oled_Init && state_d != Oled_Init);
    wire enter_clear = (state == Oled_Clear && state_d != Oled_Clear);
    wire enter_on = (state == Oled_On && state_d != Oled_On);
    wire enter_show = (state == Oled_Show && state_d != Oled_Show);

    always @(posedge clk or negedge rst) begin
        if (rst == RST_T) begin
            state_d <= Oled_Idle;
            init_req_reg <= 1'b0;
            clear_req_reg <= 1'b0;
            on_req_reg <= 1'b0;
            show_req_reg <= 1'b0;
        end
        else begin
            state_d <= state;

            // 初始化请求：只在刚进入Init状态时置1
            if (enter_init && delay_time >= 32'd500_000)
                init_req_reg <= 1'b1;
            else if (init_finish)
                init_req_reg <= 1'b0;

            // 清屏请求：只在刚进入Clear状态时置1
            if (enter_clear)
                clear_req_reg <= 1'b1;
            else if (clear_ack)
                clear_req_reg <= 1'b0;

            // 全亮请求：只在刚进入On状态时置1
            if (enter_on)
                on_req_reg <= 1'b1;
            else if (On_ack)
                on_req_reg <= 1'b0;

            // 显示文本请求：只在刚进入Show状态时置1
            if (enter_show)
                show_req_reg <= 1'b1;
            else if (show_end)
                show_req_reg <= 1'b0;
        end
    end

    assign init_req  = init_req_reg;
    assign clear_req = clear_req_reg;
    assign On_req    = on_req_reg;
    assign show_req  = show_req_reg;

    assign iic_req = (init_req || clear_req || On_req || show_req);

    // 数据多路复用（参考Oled_Top.v）
    always @(*) begin
        case (state)
            Oled_Init:
                iic_data = init_data;
            Oled_Clear:
                iic_data = clear_data;
            Oled_On:
                iic_data = On_data;
            Oled_Show:
                iic_data = show_data;
            default:
                iic_data = 24'd0;
        endcase
    end

    // 上电延时（参考Oled_Top.v）
    always @(posedge clk or negedge rst) begin
        if (rst == RST_T)
            delay_time <= 32'd0;
        else if (delay_time >= 32'd500_000)
            delay_time <= delay_time;
        else
            delay_time <= delay_time + 1'b1;
    end

    // 状态转移
    always @(posedge clk or negedge rst) begin
        if (rst == RST_T)
            state <= Oled_Idle;
        else
            state <= next_state;
    end

    // 命令完成信号生成（修复：使用边沿检测）
    reg init_finish_d;
    reg clear_ack_d;
    reg on_ack_d;
    reg show_end_d;

    always @(posedge clk or negedge rst) begin
        if (rst == RST_T) begin
            init_finish_d <= 1'b0;
            clear_ack_d <= 1'b0;
            on_ack_d <= 1'b0;
            show_end_d <= 1'b0;
            cmd_done <= 1'b0;
        end
        else begin
            init_finish_d <= init_finish;
            clear_ack_d <= clear_ack;
            on_ack_d <= On_ack;
            show_end_d <= show_end;

            // 检测完成信号的上升沿
            if ((init_finish && !init_finish_d) ||
                    (clear_ack && !clear_ack_d) ||
                    (On_ack && !on_ack_d) ||
                    (show_end && !show_end_d)) begin  // ← 添加show_end边沿检测
                cmd_done <= 1'b1;
            end
            else begin
                cmd_done <= 1'b0;
            end
        end
    end

    // 次态逻辑（添加命令触发）
    always @(*) begin
        case (state)
            Oled_Idle: begin
                if (cmd_valid && !cmd_done) begin
                    case (cmd_code)
                        CMD_OLED_INIT:
                            next_state = Oled_Init;
                        CMD_OLED_CLEAR:
                            next_state = Oled_Clear;
                        CMD_OLED_ON:
                            next_state = Oled_On;
                        CMD_OLED_SHOW:
                            next_state = Oled_Show;
                        default:
                            next_state = Oled_Idle;
                    endcase
                end
                else
                    next_state = Oled_Idle;
            end

            Oled_Init: begin
                if (init_finish == 1'b1)
                    next_state = Oled_Idle;
                else
                    next_state = Oled_Init;
            end

            Oled_Clear: begin
                if (clear_ack == 1'b1)
                    next_state = Oled_Idle;
                else
                    next_state = Oled_Clear;
            end

            Oled_On: begin
                if (On_ack == 1'b1)
                    next_state = Oled_Idle;
                else
                    next_state = Oled_On;
            end

            Oled_Show: begin
                if (show_end == 1'b1)
                    next_state = Oled_Idle;
                else
                    next_state = Oled_Show;
            end

            default:
                next_state = Oled_Idle;
        endcase
    end

    // =========================================================================
    // 模块实例化（完全参考Oled_Top.v）
    // =========================================================================

    // I2C物理层
    I2C_Master I2C_Master_V (
                   .I_Clk_in      (clk),
                   .I_Rst_n       (rst),
                   .O_SCL         (i2c_scl),
                   .IO_SDA        (i2c_sda),
                   .I_Start       (iic_req),
                   .O_Done        (w_ack),
                   .I_R_W_SET     (1'b1),  // 参考例程使用1
                   .I_Slave_Addr  (iic_data[23:17]),
                   .I_R_W_Data    (iic_data[15:0]),
                   .O_Data        (),
                   .O_Error       ()
               );

    // OLED初始化模块
    Oled_Init Oled_Init_HP (
                  .clk          (clk),
                  .rst          (rst),
                  .init_req     (init_req),
                  .write_done   (w_ack),
                  .init_finish  (init_finish),
                  .Init_data    (init_data)
              );

    // OLED清屏模块
    Oled_Clear Oled_Clear_HP (
                   .clk         (clk),
                   .rst         (rst),
                   .write_done  (w_ack),
                   .clear_req   (clear_req),
                   .clear_ack   (clear_ack),
                   .clear_data  (clear_data)
               );

    // OLED全亮显示模块
    Oled_On Oled_On_HP (
                .clk         (clk),
                .rst         (rst),
                .write_done  (w_ack),
                .On_req      (On_req),
                .On_ack      (On_ack),
                .On_data     (On_data)
            );

    // OLED显示文本控制模块
    Oled_Show_control Oled_Show_control_HP (
                          .clk          (clk),
                          .rst          (rst),
                          .show_ack     (show_ack),
                          .show_req     (show_req),
                          .show_end     (show_end),
                          .start_x      (start_x),
                          .start_y      (start_y),
                          .show_select  (show_select)
                      );

    // OLED显示信息模块
    Oled_Show_Info Oled_Show_Info_HP (
                       .clk          (clk),
                       .rst          (rst),
                       .write_done   (w_ack),
                       .start_x      (start_x),
                       .start_y      (start_y),
                       .show_select  (show_select),
                       .show_req     (show_req),
                       .show_ack     (show_ack),
                       .show_data    (show_data)
                   );

endmodule
