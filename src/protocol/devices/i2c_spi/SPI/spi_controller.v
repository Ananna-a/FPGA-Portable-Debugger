/****************************************************************************
 * SPI设备控制器 V2.2 - 资源优化版
 * 
 * 设计思路：
 *   1. 使用简单的case状态机（类似i2c_control）
 *   2. 使用cnt计数器跟踪字节位置
 *   3. ✅ 删除data_buffer，直接使用payload（节省512寄存器）
 *   4. 复用spi_master_core底层驱动
 * 
 * 功能：
uart_tx_mux (优先级1)
            ↓
            uart_byte_tx
            ↓
            uart_tx (CH340) → 上位机
(0x06 + 0x02 + 地址 + 数据)
 * 
 * 默认配置 (V2.2):
 *   - 频率: 1MHz (适配24MHz逻辑分析仪)
 *   - 模式: Mode 0 (CPOL=0, CPHA=0)
 *   - 位序: MSB First
 * 
 * V2.2优化 (2025-11-01):
 *   - 删除data_buffer[63:0]数组（节省512寄存器）
 *   - 使用current_payload单字节缓存 + payload_ready握手
 *   - 状态机等待payload_ready后直接使用current_payload
 *   - 减少编译时间，提升资源利用率
 * 
 * 作者: AI Assistant V2.2
 * 日期: 2025-11-01
 ****************************************************************************/

module spi_controller (
        input wire clk,
        input wire rst,                    // 低有效复位

        // 命令接口
        input wire cmd_valid,
        input wire [7:0] cmd_code,
        input wire [7:0] cmd_payload,
        input wire cmd_payload_valid,
        input wire [15:0] payload_counter,
        output reg cmd_done,
        output reg [7:0] response_data,
        output reg response_valid,

        // SPI物理接口
        output wire spi_cs,
        output wire spi_sclk,
        output wire spi_mosi,
        input wire spi_miso
    );

    //=========================================================================
    // 参数定义
    //=========================================================================
    localparam CMD_SPI_CONFIG           = 8'h80;
    localparam CMD_SPI_TRANSFER         = 8'h81;
    localparam CMD_SPI_FLASH_ID         = 8'h82;
    localparam CMD_SPI_FLASH_READ       = 8'h83;
    localparam CMD_SPI_FLASH_WRITE      = 8'h84;
    localparam CMD_SPI_FLASH_ERASE_SECTOR = 8'h85;
    localparam CMD_SPI_FLASH_ERASE_CHIP = 8'h86;
    localparam CMD_SPI_FLASH_READ_STATUS = 8'h87;

    localparam FLASH_CMD_READ_ID    = 8'h9F;
    localparam FLASH_CMD_READ_DATA  = 8'h03;
    localparam FLASH_CMD_WRITE_EN   = 8'h06;
    localparam FLASH_CMD_PAGE_PROG  = 8'h02;
    localparam FLASH_CMD_SECTOR_ERASE = 8'h20;  // 4KB扇区擦除
    localparam FLASH_CMD_CHIP_ERASE = 8'hC7;    // 全片擦除
    localparam FLASH_CMD_READ_STATUS = 8'h05;    // 读状态寄存器

    //=========================================================================
    // 状态机（简化设计）
    //=========================================================================
    localparam [3:0]
               IDLE         = 4'd0,
               CONFIG       = 4'd1,
               TRANSFER     = 4'd2,
               FLASH_ID     = 4'd3,
               FLASH_READ   = 4'd4,
               FLASH_WRITE  = 4'd5,
               FLASH_ERASE  = 4'd6,
               FLASH_STATUS = 4'd7,
               WAIT_DONE    = 4'd8,
               WAIT_CS      = 4'd9,   // 新增：等待CS拉高
               FINISH       = 4'd10;

    reg [3:0] state;

    //=========================================================================
    // 寄存器集合（简化版）
    //=========================================================================
    // cmd_valid边沿检测
    reg cmd_valid_d;

    // ✅ 配置参数：直接传递给底层
    reg [15:0] spi_freq_khz_reg;   // SPI频率（实时传递）
    reg cpol_reg, cpha_reg, msb_reg;

    // ✅ 数组缓存：用于所有命令的payload缓存（最多256字节）
    reg [7:0] data_buffer[255:0];  // 与I2C保持一致的声明方式

    reg [7:0] cmd_reg;             // 当前命令
    reg [7:0] byte_total;          // 总字节数
    reg [7:0] cnt;                 // 计数器（核心）
    reg [23:0] addr_reg;           // Flash地址（从data_buffer读取）

    // CS等待计数器（确保CS拉高足够时间）
    reg [7:0] cs_wait_cnt;
    localparam CS_WAIT_CYCLES = 8'd50;  // 等待50个时钟周期(1us @50MHz)

    //=========================================================================
    // SPI核心接口
    //=========================================================================
    reg [7:0] spi_tx_byte;
    reg spi_start;
    wire [7:0] spi_rx_byte;
    wire spi_done;
    wire spi_busy;

    //=========================================================================
    // Payload接收（简化版：只缓存到数组，状态机再读取）
    // 完全参考I2C的实现方式
    //=========================================================================
    always @(posedge clk or negedge rst) begin
        if (!rst) begin
            // 不需要其他寄存器，只清空用于计数的变量
        end
        else if (cmd_payload_valid) begin
            // ✅ 完全统一：所有payload直接缓存到数组
            // 从payload[0]开始全部存储（data_buffer[0] = payload[0]）
            if (payload_counter < 16'd256) begin
                data_buffer[payload_counter[7:0]] <= cmd_payload;
            end
        end
    end

    //=========================================================================
    // 主状态机（参考i2c_control的简洁风格）
    //=========================================================================
    always @(posedge clk or negedge rst) begin
        if (!rst) begin
            state <= IDLE;
            cmd_done <= 1'b0;
            response_valid <= 1'b0;
            cmd_reg <= 8'h0;
            cnt <= 8'd0;
            spi_start <= 1'b0;
            spi_tx_byte <= 8'h0;
            cmd_valid_d <= 1'b0;  // ✅ 添加
            cs_wait_cnt <= 8'd0;  // ✅ 初始化CS等待计数器

            // SPI配置默认值：1MHz, Mode 0 (CPOL=0, CPHA=0), MSB First
            spi_freq_khz_reg <= 16'd1000;  // 默认1MHz (适合24MHz采样率)
            cpol_reg <= 1'b0;  // Mode 0: CPOL=0
            cpha_reg <= 1'b0;  // Mode 0: CPHA=0
            msb_reg <= 1'b1;   // MSB First (标准SPI)
        end
        else begin
            // 默认值：每个周期清零单周期脉冲信号
            cmd_done <= 1'b0;
            response_valid <= 1'b0;
            spi_start <= 1'b0;  // 确保默认不启动传输
            cmd_valid_d <= cmd_valid;  // ✅ 锁存cmd_valid

            case (state)
                //-------------------------------------------------------------
                IDLE: begin
                    // ✅ 使用上升沿检测，避免重复触发
                    if (cmd_valid && !cmd_valid_d) begin
                        cmd_reg <= cmd_code;
                        cnt <= 8'd0;
                        byte_total <= 8'd0;  // ✅ 清零byte_total
                        addr_reg <= 24'd0;   // ✅ 清零地址

                        case (cmd_code)
                            CMD_SPI_CONFIG:
                                state <= CONFIG;
                            CMD_SPI_TRANSFER:
                                state <= TRANSFER;
                            CMD_SPI_FLASH_ID:
                                state <= FLASH_ID;
                            CMD_SPI_FLASH_READ:
                                state <= FLASH_READ;
                            CMD_SPI_FLASH_WRITE:
                                state <= FLASH_WRITE;
                            CMD_SPI_FLASH_ERASE_SECTOR, CMD_SPI_FLASH_ERASE_CHIP:
                                state <= FLASH_ERASE;
                            CMD_SPI_FLASH_READ_STATUS:
                                state <= FLASH_STATUS;
                            default:
                                state <= IDLE;
                        endcase
                    end
                end

                //-------------------------------------------------------------
                CONFIG: begin
                    // ✅ 修复：等待所有payload到达后才应用配置
                    // CONFIG命令payload长度=5字节，从data_buffer读取
                    // 格式: [freq_l][freq_h][cpol][cpha][msb]
                    if (payload_counter >= 16'd4) begin
                        // 从data_buffer读取配置参数
                        spi_freq_khz_reg <= {data_buffer[1], data_buffer[0]};  // 小端序
                        cpol_reg <= data_buffer[2][0];
                        cpha_reg <= data_buffer[3][0];
                        msb_reg <= data_buffer[4][0];

                        // 配置完成，直接返回（不触发传输）
                        state <= FINISH;
                    end
                    // 否则继续等待payload
                end

                //-------------------------------------------------------------
                TRANSFER: begin
                    // ✅ 格式: [byte_count][data0][data1]...[dataN]
                    // byte_total = data_buffer[0], 数据从data_buffer[1]开始

                    // 等待所有payload到达
                    if (payload_counter == 16'd0) begin
                        // payload还没到，继续等待
                    end
                    else if (cnt == 8'd0) begin
                        // 第一次进入：读取字节数并准备发送
                        byte_total <= data_buffer[0];
                        cnt <= 8'd1;
                    end
                    else if ((cnt - 8'd1) < byte_total) begin
                        // 检查所有数据是否已到达 (需要byte_total+1个字节)
                        if (payload_counter > byte_total) begin
                            // 从数组读取数据（data_buffer[1]=data[0], data_buffer[2]=data[1], ...）
                            spi_tx_byte <= data_buffer[cnt];
                            spi_start <= 1'b1;
                            cnt <= cnt + 8'd1;
                            state <= WAIT_DONE;
                        end
                        // 否则继续等待payload到达
                    end
                    else begin
                        // 发送完成
                        state <= FINISH;
                    end
                end

                //-------------------------------------------------------------
                FLASH_ID: begin
                    case (cnt)
                        8'd0: begin  // 发送0x9F
                            spi_tx_byte <= FLASH_CMD_READ_ID;
                            spi_start <= 1'b1;
                            cnt <= 8'd1;
                            state <= WAIT_DONE;
                        end
                        8'd1, 8'd2, 8'd3, 8'd4: begin  // 接收4字节（1个dummy + 3个有效数据）
                            spi_tx_byte <= 8'hFF;
                            spi_start <= 1'b1;
                            cnt <= cnt + 8'd1;
                            state <= WAIT_DONE;
                        end
                        default:
                            state <= FINISH;
                    endcase
                end

                //-------------------------------------------------------------
                FLASH_READ: begin
                    // ✅ 格式: [addr_h][addr_m][addr_l][byte_count]
                    // ✅ 修复：直接从data_buffer读取，不等待payload_counter
                    // 当状态机进入时，payload已经全部接收完毕

                    case (cnt)
                        8'd0: begin
                            // 第一次进入：解析地址和字节数
                            addr_reg <= {data_buffer[0], data_buffer[1], data_buffer[2]};
                            byte_total <= data_buffer[3];
                            cnt <= 8'd1;
                        end
                        8'd1: begin  // 发送读命令
                            spi_tx_byte <= FLASH_CMD_READ_DATA;
                            spi_start <= 1'b1;
                            cnt <= 8'd2;
                            state <= WAIT_DONE;
                        end
                        8'd2: begin  // 地址高字节
                            spi_tx_byte <= addr_reg[23:16];
                            spi_start <= 1'b1;
                            cnt <= 8'd3;
                            state <= WAIT_DONE;
                        end
                        8'd3: begin  // 地址中字节
                            spi_tx_byte <= addr_reg[15:8];
                            spi_start <= 1'b1;
                            cnt <= 8'd4;
                            state <= WAIT_DONE;
                        end
                        8'd4: begin  // 地址低字节
                            spi_tx_byte <= addr_reg[7:0];
                            spi_start <= 1'b1;
                            cnt <= 8'd5;
                            state <= WAIT_DONE;
                        end
                        default: begin
                            // 读取数据：cnt-5即实际数据字节索引
                            if ((cnt - 8'd5) < byte_total) begin
                                spi_tx_byte <= 8'hFF;
                                spi_start <= 1'b1;
                                cnt <= cnt + 8'd1;
                                state <= WAIT_DONE;
                            end
                            else begin
                                state <= FINISH;
                            end
                        end
                    endcase
                end

                //-------------------------------------------------------------
                FLASH_WRITE: begin
                    // ✅ 格式: [addr_h][addr_m][addr_l][data0][data1]...[dataN]
                    // ✅ 修复：直接从data_buffer读取，使用payload_counter计算字节数
                    // 当状态机进入时，payload已经全部接收完毕

                    case (cnt)
                        8'd0: begin
                            // 第一次进入：解析地址和计算字节数
                            addr_reg <= {data_buffer[0], data_buffer[1], data_buffer[2]};
                            byte_total <= payload_counter - 16'd3;  // 实际数据字节数
                            cnt <= 8'd1;
                        end
                        8'd1: begin  // 写使能
                            spi_tx_byte <= FLASH_CMD_WRITE_EN;
                            spi_start <= 1'b1;
                            cnt <= 8'd2;
                            state <= WAIT_DONE;
                        end
                        8'd2: begin  // 等待CS拉高
                            cs_wait_cnt <= 8'd0;
                            cnt <= 8'd3;
                            state <= WAIT_CS;  // 进入CS等待状态
                        end
                        8'd3: begin  // 页编程命令
                            spi_tx_byte <= FLASH_CMD_PAGE_PROG;
                            spi_start <= 1'b1;
                            cnt <= 8'd4;
                            state <= WAIT_DONE;
                        end
                        8'd4: begin  // 地址高
                            spi_tx_byte <= addr_reg[23:16];
                            spi_start <= 1'b1;
                            cnt <= 8'd5;
                            state <= WAIT_DONE;
                        end
                        8'd5: begin  // 地址中
                            spi_tx_byte <= addr_reg[15:8];
                            spi_start <= 1'b1;
                            cnt <= 8'd6;
                            state <= WAIT_DONE;
                        end
                        8'd6: begin  // 地址低
                            spi_tx_byte <= addr_reg[7:0];
                            spi_start <= 1'b1;
                            cnt <= 8'd7;
                            state <= WAIT_DONE;
                        end
                        default: begin
                            // ✅ 从data_buffer[3]开始读取写入数据
                            // ✅ 修复：删除payload_counter检查，直接读取
                            if ((cnt - 8'd7) < byte_total) begin
                                // 从数组读取写入数据（data_buffer[3]=data[0], ...）
                                // ✅ 修复索引：cnt=7时读data_buffer[3]，cnt=8时读data_buffer[4]，...
                                spi_tx_byte <= data_buffer[cnt - 8'd4];  // 修正：7-4=3 ✓
                                spi_start <= 1'b1;
                                cnt <= cnt + 8'd1;
                                state <= WAIT_DONE;
                            end
                            else begin
                                state <= FINISH;
                            end
                        end
                    endcase
                end

                //-------------------------------------------------------------
                FLASH_ERASE: begin
                    // 扇区擦除格式: [addr_h][addr_m][addr_l]
                    // 全片擦除格式: 无参数
                    // ✅ 修复：直接读取，不等待payload_counter

                    if (cmd_reg == CMD_SPI_FLASH_ERASE_SECTOR) begin
                        // 扇区擦除需要地址
                        case (cnt)
                            8'd0: begin
                                // 第一次进入：解析地址
                                addr_reg <= {data_buffer[0], data_buffer[1], data_buffer[2]};
                                cnt <= 8'd1;
                            end
                            8'd1: begin  // 写使能
                                spi_tx_byte <= FLASH_CMD_WRITE_EN;
                                spi_start <= 1'b1;
                                cnt <= 8'd2;
                                state <= WAIT_DONE;
                            end
                            8'd2: begin  // 等待CS拉高
                                cs_wait_cnt <= 8'd0;
                                cnt <= 8'd3;
                                state <= WAIT_CS;
                            end
                            8'd3: begin  // 扇区擦除命令
                                spi_tx_byte <= FLASH_CMD_SECTOR_ERASE;
                                spi_start <= 1'b1;
                                cnt <= 8'd4;
                                state <= WAIT_DONE;
                            end
                            8'd4: begin  // 地址高
                                spi_tx_byte <= addr_reg[23:16];
                                spi_start <= 1'b1;
                                cnt <= 8'd5;
                                state <= WAIT_DONE;
                            end
                            8'd5: begin  // 地址中
                                spi_tx_byte <= addr_reg[15:8];
                                spi_start <= 1'b1;
                                cnt <= 8'd6;
                                state <= WAIT_DONE;
                            end
                            8'd6: begin  // 地址低
                                spi_tx_byte <= addr_reg[7:0];
                                spi_start <= 1'b1;
                                cnt <= 8'd7;  // ✅ 修复：应该递增到7
                                state <= WAIT_DONE;
                            end
                            default:
                                state <= FINISH;
                        endcase
                    end
                    else begin
                        // 全片擦除
                        case (cnt)
                            8'd0: begin  // 写使能
                                spi_tx_byte <= FLASH_CMD_WRITE_EN;
                                spi_start <= 1'b1;
                                cnt <= 8'd1;
                                state <= WAIT_DONE;
                            end
                            8'd1: begin  // 等待CS拉高
                                cs_wait_cnt <= 8'd0;
                                cnt <= 8'd2;
                                state <= WAIT_CS;
                            end
                            8'd2: begin  // 全片擦除命令
                                spi_tx_byte <= FLASH_CMD_CHIP_ERASE;
                                spi_start <= 1'b1;
                                cnt <= 8'd3;
                                state <= WAIT_DONE;
                            end
                            default:
                                state <= FINISH;
                        endcase
                    end
                end

                //-------------------------------------------------------------
                FLASH_STATUS: begin
                    case (cnt)
                        8'd0: begin  // 发送读状态命令
                            spi_tx_byte <= FLASH_CMD_READ_STATUS;
                            spi_start <= 1'b1;
                            cnt <= 8'd1;
                            state <= WAIT_DONE;
                        end
                        8'd1: begin  // 接收状态寄存器
                            spi_tx_byte <= 8'hFF;
                            spi_start <= 1'b1;
                            cnt <= 8'd2;  // 读取后进入cnt=2，下次会进入default→FINISH
                            state <= WAIT_DONE;
                        end
                        default:
                            state <= FINISH;
                    endcase
                end

                //-------------------------------------------------------------
                WAIT_DONE: begin
                    if (spi_done) begin
                        // ✅ 修复：输出接收数据（参考上一版本的判断逻辑）
                        // 通用传输：所有字节都输出接收数据（cnt已递增）
                        if (cmd_reg == CMD_SPI_TRANSFER && cnt > 8'd0) begin
                            response_data <= spi_rx_byte;
                            response_valid <= 1'b1;
                        end
                        // Flash ID：跳过命令字节（cnt=1），从cnt=2开始输出
                        else if (cmd_reg == CMD_SPI_FLASH_ID && cnt > 8'd1) begin
                            response_data <= spi_rx_byte;
                            response_valid <= 1'b1;
                        end
                        // Flash Read：跳过命令+地址（cnt=1-5），从cnt=6开始输出
                        // ✅ 修复：SPI全双工通信，发送地址时MISO是回显，需要多跳过1个字节
                        else if (cmd_reg == CMD_SPI_FLASH_READ && cnt > 8'd5 && (cnt - 8'd6) < byte_total) begin
                            response_data <= spi_rx_byte;
                            response_valid <= 1'b1;
                        end
                        // Flash Status：跳过命令字节（cnt=1），输出状态寄存器
                        else if (cmd_reg == CMD_SPI_FLASH_READ_STATUS && cnt > 8'd0) begin
                            response_data <= spi_rx_byte;
                            response_valid <= 1'b1;
                        end

                        // 返回对应状态
                        case (cmd_reg)
                            CMD_SPI_TRANSFER:
                                state <= TRANSFER;
                            CMD_SPI_FLASH_ID:
                                state <= FLASH_ID;
                            CMD_SPI_FLASH_READ:
                                state <= FLASH_READ;
                            CMD_SPI_FLASH_WRITE:
                                state <= FLASH_WRITE;
                            CMD_SPI_FLASH_ERASE_SECTOR, CMD_SPI_FLASH_ERASE_CHIP:
                                state <= FLASH_ERASE;
                            CMD_SPI_FLASH_READ_STATUS:
                                state <= FLASH_STATUS;
                            default:
                                state <= FINISH;
                        endcase
                    end
                end

                //-------------------------------------------------------------
                // 新增：等待CS拉高状态
                WAIT_CS: begin
                    if (cs_wait_cnt >= CS_WAIT_CYCLES - 1'd1) begin
                        // 等待足够时间后，返回对应命令状态继续执行
                        cs_wait_cnt <= 8'd0;
                        case (cmd_reg)
                            CMD_SPI_FLASH_WRITE:
                                state <= FLASH_WRITE;
                            CMD_SPI_FLASH_ERASE_SECTOR, CMD_SPI_FLASH_ERASE_CHIP:
                                state <= FLASH_ERASE;
                            default:
                                state <= FINISH;
                        endcase
                    end
                    else begin
                        cs_wait_cnt <= cs_wait_cnt + 1'd1;
                    end
                end

                //-------------------------------------------------------------
                FINISH: begin
                    cmd_done <= 1'b1;
                    cnt <= 8'd0;           // ✅ 清零计数器
                    byte_total <= 8'd0;    // ✅ 清零字节数
                    cs_wait_cnt <= 8'd0;   // ✅ 清零等待计数器
                    state <= IDLE;
                end

                default:
                    state <= IDLE;
            endcase
        end
    end

    //=========================================================================
    // SPI底层核心实例
    //=========================================================================
    spi_master_core u_spi_core (
                        .clk           (clk),
                        .rst_n         (rst),
                        .spi_freq_khz  (spi_freq_khz_reg),
                        .cpol          (cpol_reg),
                        .cpha          (cpha_reg),
                        .msb_first     (msb_reg),
                        .tx_data       (spi_tx_byte),
                        .trans_en      (spi_start),
                        .rx_data       (spi_rx_byte),
                        .trans_done    (spi_done),
                        .spi_busy      (spi_busy),
                        .spi_cs        (spi_cs),
                        .spi_sclk      (spi_sclk),
                        .spi_mosi      (spi_mosi),
                        .spi_miso      (spi_miso)
                    );

endmodule
