/****************************************************************************
 * 通用I2C主机发送控制器 - 简化版（仅支持写入）
 * 
 * 功能：
 *   - 0x70: I2C主机写入 - 向指定地址写入多字节数据
 * 
 * 数据格式：
 *   写入: [dev_addr(7bit)][byte_count][data0][data1]...[dataN]
 *   
 * 说明：
 *   - 仅实现I2C主机发送时序（写功能）
 *   - 不支持读取和扫描功能
 *   - 配合上位机通用I2C设备面板使用
 * 
 * 基于参考模块: i2c_control.v + i2c_bit_shift.v
 * 
 * 作者: AI Assistant
 * 日期: 2025-11-01
 ****************************************************************************/

module i2c_generic_controller (
        input wire clk,                    // 系统时钟 50MHz
        input wire rst,                    // 复位信号（低有效）

        // 命令接口
        input wire cmd_valid,              // 命令有效（单周期脉冲）
        input wire [7:0] cmd_code,         // 命令码: 0x70/0x71/0x72
        input wire [7:0] cmd_payload,      // Payload数据（逐字节接收）
        input wire cmd_payload_valid,      // Payload有效
        input wire [15:0] payload_counter, // Payload计数器
        output reg cmd_done,               // 命令完成
        output reg [7:0] response_data,    // 响应数据（扫描结果或读取数据）

        // I2C物理接口
        output wire i2c_scl,               // SCL时钟线
        inout wire i2c_sda                 // SDA数据线
    );

    // 命令码定义
    localparam CMD_I2C_WRITE = 8'h70;  // I2C主机写入（唯一支持的命令）

    // 状态机定义（简化版，只保留写操作相关状态）
    localparam [2:0]
               S_IDLE        = 3'd0,   // 空闲
               S_WAIT_DATA   = 3'd1,   // 等待payload稳定
               S_WRITE       = 3'd2,   // 写操作（准备数据）
               S_WRITE_REQ   = 3'd3,   // 写操作（发起请求）
               S_WRITE_WAIT  = 3'd4,   // 等待写完成
               S_DONE        = 3'd5;   // 完成

    reg [2:0] state;

    // 命令参数缓存
    reg [7:0] dev_addr;         // 7位设备地址
    reg [7:0] byte_count;       // 写入字节数
    reg [7:0] data_buffer[255:0]; // 数据缓冲区（最多255字节）
    reg [7:0] data_index;       // 数据索引

    // i2c_control接口
    reg wrreg_req;              // 写请求
    reg [15:0] addr;            // 寄存器地址（本应用不使用，固定为0）
    reg addr_mode;              // 地址模式（本应用不使用，固定为0）
    reg [7:0] wrdata;           // 写入数据
    reg [7:0] device_id;        // 设备ID（7位地址 + R/W位）
    wire RW_Done;               // 读写完成
    wire ack;                   // ACK应答

    //=========================================================================
    // Payload接收逻辑（独立于状态机，持续接收）
    //=========================================================================
    always @(posedge clk or negedge rst) begin
        if (!rst) begin
            dev_addr <= 8'd0;
            byte_count <= 8'd0;
        end
        else begin
            // 持续接收payload数据（不依赖状态）
            if (cmd_payload_valid) begin
                case (payload_counter)
                    16'd0:
                        dev_addr <= cmd_payload;      // 第1字节: 7位设备地址
                    16'd1:
                        byte_count <= cmd_payload;    // 第2字节: 字节数
                    default: begin
                        // 第3字节开始: 写入数据
                        if (payload_counter >= 16'd2 && payload_counter < 16'd257) begin
                            data_buffer[payload_counter - 16'd2] <= cmd_payload;
                        end
                    end
                endcase
            end
        end
    end

    //=========================================================================
    // 主状态机 - 仅实现I2C主机写入时序
    //=========================================================================
    always @(posedge clk or negedge rst) begin
        if (!rst) begin
            state <= S_IDLE;
            cmd_done <= 1'b0;
            response_data <= 8'd0;
            wrreg_req <= 1'b0;
            addr <= 16'd0;
            addr_mode <= 1'b0;
            wrdata <= 8'd0;
            device_id <= 8'd0;
            data_index <= 8'd0;
        end
        else begin
            case (state)
                // ============ 空闲状态 ============
                S_IDLE: begin
                    cmd_done <= 1'b0;
                    wrreg_req <= 1'b0;

                    if (cmd_valid) begin
                        state <= S_WAIT_DATA;  // 等待payload数据稳定
                        data_index <= 8'd0;
                    end
                end

                // ============ 等待数据稳定 ============
                S_WAIT_DATA: begin
                    // 等待1个周期，确保dev_addr和byte_count已经稳定
                    // 准备I2C写操作
                    device_id <= {dev_addr[6:0], 1'b0};  // 7位地址 + 写标志(0)
                    addr_mode <= 1'b0;  // 不使用寄存器地址模式
                    addr <= 16'd0;      // 地址固定为0
                    state <= S_WRITE;
                end

                // ============ I2C写操作 - 准备数据 ============
                S_WRITE: begin
                    if (data_index < byte_count) begin
                        // 准备当前字节数据（等待1个周期让wrdata稳定）
                        wrdata <= data_buffer[data_index];
                        state <= S_WRITE_REQ;
                    end
                    else begin
                        // 所有字节写完成
                        state <= S_DONE;
                    end
                end

                // ============ I2C写操作 - 发起请求 ============
                S_WRITE_REQ: begin
                    // 发起I2C写请求（此时wrdata已稳定）
                    wrreg_req <= 1'b1;
                    state <= S_WRITE_WAIT;
                end

                // ============ I2C写操作 - 等待完成 ============
                S_WRITE_WAIT: begin
                    if (RW_Done) begin
                        wrreg_req <= 1'b0;
                        data_index <= data_index + 1'b1;
                        state <= S_WRITE;  // 继续写下一个字节
                    end
                end

                // ============ 完成状态 ============
                S_DONE: begin
                    cmd_done <= 1'b1;
                    response_data <= 8'd0;  // 写操作无返回数据
                    state <= S_IDLE;
                end

                default:
                    state <= S_IDLE;
            endcase
        end
    end

    //=========================================================================
    // I2C控制模块实例化（使用参考模块）
    //=========================================================================
    i2c_control u_i2c_control (
                    .Clk         (clk),
                    .Rst_n       (rst),
                    .wrreg_req   (wrreg_req),
                    .rdreg_req   (1'b0),        // 只写不读，固定为0
                    .addr        (addr),
                    .addr_mode   (addr_mode),
                    .wrdata      (wrdata),
                    .rddata      (),            // 读数据悬空
                    .device_id   (device_id),
                    .RW_Done     (RW_Done),
                    .ack         (ack),
                    .i2c_sclk    (i2c_scl),
                    .i2c_sdat    (i2c_sda)
                );

endmodule
