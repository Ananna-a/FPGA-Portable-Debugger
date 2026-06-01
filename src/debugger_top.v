//=============================================================================
// 多功能调试器 - FPGA顶层模块 (集成ADC+DDR3版本)
// 版本: V3.0 - 完整功能版
// 当前实现: DDS函数发生器 + ADC示波器 + PWM + 序列发生器 + I2C/SPI + 蓝牙
// 更新: 2025-11-01 - 集成ADC采集和DDR3缓存功能
//=============================================================================

module debugger_top(
        // 时钟和复位
        input clk,              // 50MHz主时钟
        input reset_n,

        // USB CDC接口 (FX2)
        inout [7:0] fx2_fdata,
        input fx2_flagb,
        input fx2_flagc,
        input fx2_ifclk,
        output [1:0] fx2_faddr,
        output fx2_sloe,
        output fx2_slwr,
        output fx2_slrd,
        output fx2_pkt_end,
        output fx2_slcs,

        // UART接口 (CH340 - 应答+蓝牙透传)
        output uart_tx,         // UART TX to CH340
        input uart_rx,          // UART RX from CH340 (预留)

        // 蓝牙串口接口
        output bt_tx,           // 蓝牙TX (FPGA->蓝牙模块)
        input bt_rx,            // 蓝牙RX (蓝牙模块->FPGA)

        // 调试接口
        output [7:0] led,       // 8个状态LED
        input SW,               // 切换开关
        output sh_cp,           // 数码管595时钟
        output st_cp,           // 数码管595锁存
        output ds,              // 数码管595数据

        // DAC输出接口 (DDS双通道)
        output [7:0] DA0_Data,  // DAC通道A数据
        output [7:0] DA1_Data,  // DAC通道B数据
        output DA0_Clk,         // DAC通道A时钟
        output DA1_Clk,         // DAC通道B时钟

        // 自定义序列输出接口 (SEQ 8通道)
        output [7:0] SEQ_OUT,   // 8通道序列输出（并行/串行模式）

        // PWM输出接口 (PWM 8通道)
        output [7:0] PWM_OUT,   // 8通道PWM输出

        // 逻辑分析仪输入接口 (8通道数字输入)
        input [7:0] LOGIC_IN,    // 8通道逻辑分析仪输入

        // I2C接口 (OLED SSD1306)
        inout i2c_sda,           // I2C数据线（L18）
        output i2c_scl,          // I2C时钟线（M20）

        // SPI接口 (W25Q128 Flash)
        output spi_cs,           // SPI片选（B2）
        output spi_sclk,         // SPI时钟（B1）
        output spi_mosi,         // SPI主出从入（M17）
        input spi_miso,          // SPI主入从出（A1）

        // 1-Wire接口 (DS18B20温度传感器)
        inout ds18b20_dq,        // DS18B20单总线数据线（需在CST文件中分配引脚）

        // CAN总线接口 (SIT1042)
        output can_tx,           // CAN总线发送（FPGA->CAN收发器）
        input can_rx,            // CAN总线接收（CAN收发器->FPGA）

        // ========== ADC接口（双通道并行采样） ==========
        input  wire [7:0]   adc_data_a,     // ADC通道1数据输入
        input  wire [7:0]   adc_data_b,     // ADC通道2数据输入
        output wire         adc_clk_out_a,  // ADC通道1时钟输出（50MHz, 180度相位）
        output wire         adc_clk_out_b,  // ADC通道2时钟输出（50MHz, 180度相位）

        // ========== 以太网物理接口（RGMII仅TX发送） ==========
        output              rgmii_tx_clk,   // RGMII发送时钟输出
        output [3:0]        rgmii_txd,      // RGMII发送数据
        output              rgmii_txen,     // RGMII发送使能
        output              eth_rst_n,      // 以太网PHY复位（高电平有效）

        // ========== 新增：DDR3物理接口 ==========
        output [13:0]       O_ddr_addr,
        output [2:0]        O_ddr_ba,
        output              O_ddr_cs_n,
        output              O_ddr_ras_n,
        output              O_ddr_cas_n,
        output              O_ddr_we_n,
        output              O_ddr_clk,
        output              O_ddr_clk_n,
        output              O_ddr_cke,
        output              O_ddr_odt,
        output              O_ddr_reset_n,
        output [1:0]        O_ddr_dqm,
        inout  [15:0]       IO_ddr_dq,
        inout  [1:0]        IO_ddr_dqs,
        inout  [1:0]        IO_ddr_dqs_n
    );

    //=========================================================================
    // 时钟和复位
    //=========================================================================
    wire clk_fx2;
    assign clk_fx2 = fx2_ifclk;

    // PLL生成125MHz DDS时钟 和 50MHz ADC时钟
    wire clk125m;               // 125MHz DDS时钟
    wire adc_clk_50m;           // 50MHz ADC时钟(180°相位)
    wire pll_lock;              // PLL锁定信号

    Gowin_PLL u_pll_dds_clk(
                  .clkin(clk),            // 50MHz输入
                  .init_clk(clk),         // 初始化时钟
                  .clkout0(clk125m),      // 125MHz输出 (DDS使用)
                  .clkout1(adc_clk_50m),  // 50MHz输出 (ADC使用, 180°相位)
                  .lock(pll_lock),        // PLL锁定指示
                  .reset(~reset_n)        // PLL复位信号
              );

    // DDR3 PLL - 生成400MHz DDR3参考时钟
    wire pll_stop;               // DDR3 IP核反馈信号 -> PLL使能控制
    wire pll_lock_ddr;           // DDR3 PLL锁定信号
    wire loc_clk400m;            // 400MHz DDR3参考时钟

    ddr_pll u_ddr_pll(
                .clkin(clk),                 // 50MHz输入时钟
                .init_clk(clk),              // 初始化时钟
                .enclk0(),                   // clkout0使能（未使用）
                .enclk1(),                   // clkout1使能（未使用）
                .enclk2(pll_stop),           // clkout2使能（DDR3 IP核反馈控制）
                .clkout0(),                  // clkout0未使用
                .clkout1(),                  // clkout1未使用
                .clkout2(loc_clk400m),       // clkout2输出400MHz DDR3参考时钟
                .lock(pll_lock_ddr),         // DDR3 PLL锁定信号
                .reset(~reset_n)             // PLL复位信号
            );

    // 全局复位逻辑: 外部复位 或 PLL未锁定
    wire sys_rst;
    assign sys_rst = ~reset_n | ~pll_lock;

    //=========================================================================
    // 以太网时钟和控制（仅TX发送，无RX接收）
    //=========================================================================
    // 以太网PHY控制信号
    assign eth_rst_n = 1'b1;   // PHY始终使能

    // 以太网发送时钟（直接复用DDS的125MHz时钟，无需额外PLL）
    wire clk125m_eth;
    assign clk125m_eth = clk125m;  // 复用已有的125MHz时钟用于GMII TX

    //=========================================================================
    // CDC通信链路信号
    //=========================================================================
    // 接收FIFO (FX2 -> FPGA)
    wire [7:0] rx_data;
    wire rx_valid;
    wire rx_empty;
    wire rx_full;               // 接收FIFO满
    wire [7:0] rx_fifo_out;     // 接收FIFO输出

    // 发送FIFO (FPGA -> FX2)
    wire [7:0] tx_data;
    wire tx_valid;
    wire tx_empty;
    wire tx_full;
    wire tx_rd_req;
    wire [7:0] tx_fifo_out;     // 发送FIFO输出

    // 数据包控制
    wire pkt_end;
    reg pkt_end_sync1, pkt_end_sync2;

    //=========================================================================
    // 命令解析信号
    //=========================================================================
    wire [7:0] cmd_code;        // 命令码
    wire [15:0] cmd_length;     // Payload长度
    wire [7:0] cmd_payload;     // Payload数据（逐字节）
    wire cmd_payload_valid;     // Payload有效
    wire cmd_done;              // 命令解析完成
    wire cmd_error;             // 命令错误
    wire cmd_valid_pulse;       // 命令码有效脉冲

    //=========================================================================
    // 命令处理器信号
    //=========================================================================
    wire [7:0] resp_cmd;        // 应答命令码
    wire [15:0] resp_length;    // 应答长度
    wire [7:0] resp_payload;    // 应答数据
    wire resp_valid;            // 应答有效
    wire resp_done;             // 应答完成

    //=========================================================================
    // UART接收信号 (预留)
    //=========================================================================
    wire [7:0] uart_rx_data;    // UART接收数据
    wire uart_rx_done;          // UART接收完成标志

    //=========================================================================
    // 蓝牙串口信号
    //=========================================================================
    wire [7:0] bt_status;       // 蓝牙状态
    reg bt_enable;              // 蓝牙使能(默认使能)
    reg [31:0] bt_baud_rate;    // 蓝牙波特率配置寄存器

    // UART发送多路复用信号
    wire [7:0] uart_tx_data_mux;    // 多路复用后的数据
    wire uart_tx_send_en_mux;       // 多路复用后的使能
    wire uart_tx_done_internal;     // 底层UART发送完成
    wire [2:0] uart_mux_channel;    // 当前通道（扩展到3位，支持5个通道）

    // 应答帧通道
    wire resp_tx_done_mux;          // 应答发送完成(来自MUX)

    // 蓝牙透传通道
    wire [7:0] bt_tx_data_ch;       // 蓝牙透传数据
    wire bt_tx_send_en_ch;          // 蓝牙透传使能
    wire bt_tx_done_mux;            // 蓝牙发送完成(来自MUX)

    //=========================================================================
    // UART应答信号
    //=========================================================================
    reg response_valid;         // 触发UART应答
    reg [7:0] response_mod_id;  // 应答模块ID
    reg [7:0] response_func_id; // 应答功能ID
    reg [7:0] response_status;  // 应答状态码
    wire response_done_uart;    // UART应答完成

    wire [7:0] uart_tx_data;    // UART发送数据
    wire uart_tx_send_en;       // UART发送使能
    wire uart_tx_done;          // UART发送完成标志

    // I2C通用控制器信号（提前声明避免隐式声明警告）
    wire [7:0] i2c_generic_response;  // I2C读取/扫描结果
    wire i2c_generic_cmd_done;        // I2C命令完成
    wire oled_cmd_done;               // OLED命令完成
    wire spi_cmd_done;                // SPI命令完成

    // SPI Flash读取：流式数据发送（参考IIC EEPROM）
    reg spi_data_tx_en;         // SPI数据发送使能
    reg [7:0] spi_data_byte;    // 当前发送的字节
    wire spi_data_uart_done;    // SPI数据通道UART发送完成

    // DSA数字信号测量：流式数据发送（类似SPI）
    reg dsa_data_tx_en;         // DSA数据发送使能
    reg [7:0] dsa_data_byte;    // 当前发送的字节
    wire dsa_data_uart_done;    // DSA数据通道UART发送完成
    // DSA不再需要dsa_byte_cnt和dsa_fifo（改用50MHz简化设计）

    // 🔥 V8.8.0新增：DSA全局发送使能标志（解决停止后残留数据阻塞UART问题）
    reg dsa_global_tx_enable;   // DSA发送总闸：0x66打开，0x67强制关闭

    //=========================================================================
    // 🎯 V9.2新增：Bode分析仪控制寄存器（0xB0-0xB3命令）
    //=========================================================================
    reg [31:0]  bode_freq_start;        // 扫频起始频率 (Hz)
    reg [31:0]  bode_freq_stop;         // 扫频终止频率 (Hz)
    reg [15:0]  bode_freq_steps;        // 频率点数
    reg [31:0]  bode_samples_per_freq;  // 每频点采样数
    reg         bode_param_valid;       // 参数有效脉冲
    reg         bode_sweep_enable;      // 扫频启动命令
    reg         bode_sweep_stop;        // 扫频停止命令
    
    // Bode参数锁存器（解决payload接收时的清零问题）
    reg [31:0]  bode_freq_start_latch;
    reg [31:0]  bode_freq_stop_latch;
    reg [15:0]  bode_freq_steps_latch;
    reg [31:0]  bode_samples_latch;
    
    // Bode分析仪状态（来自bode_analyzer_top）
    wire        bode_sweep_active;      // 扫频进行中
    wire        bode_data_ready;        // 数据就绪
    wire [15:0] bode_current_index;     // 当前频点索引
    wire [31:0] bode_current_freq;      // 当前频率
    
    // Bode分析仪UART接口
    wire [7:0]  bode_uart_tx_data;
    wire        bode_uart_tx_send_en;
    wire        bode_uart_tx_done;      // UART发送完成信号（来自uart_tx_mux）
    wire        bode_iq_valid;          // 🔥 V9.2.16: IQ解调输出有效（展宽后）
    wire        bode_demod_enable;      // 🔥 V9.2.4: 解调使能
    wire        bode_formatter_busy;    // 🔥 V9.2.17: formatter状态
    wire        bode_uart_tx_send_active; // 🔥 V9.2.17: UART发送请求
    wire [2:0]  bode_formatter_state;   // 🔥 V9.2.18: formatter状态机状态
    
    // 🔥 V9.2新增：Bode分析仪DDS激励控制信号
    wire [31:0] bode_dds_freq_word;     // Bode扫频频率字
    wire [8:0]  bode_dds_phase;         // Bode DDS相位
    wire [7:0]  bode_dds_amplitude;     // Bode DDS幅度
    wire        bode_dds_enable;        // Bode DDS使能（扫频时=1）
    
    // 🔥 V9.2修复：ADC采集状态跨时钟域同步（50MHz → 125MHz）
    reg         adc_ch1_active_sync1;   // 第一级同步寄存器
    reg         adc_ch1_active_sync2;   // 第二级同步寄存器（稳定输出）
    
    always @(posedge clk125m or negedge reset_n) begin
        if (!reset_n) begin
            adc_ch1_active_sync1 <= 1'b0;
            adc_ch1_active_sync2 <= 1'b0;
        end else begin
            adc_ch1_active_sync1 <= adc_ch1_stream_active;  // 第一级：捕获50MHz信号
            adc_ch1_active_sync2 <= adc_ch1_active_sync1;   // 第二级：消除亚稳态
        end
    end
    
    // 🔥 V9.2.2新增：ADC数据和有效信号跨时钟域同步（50MHz → 125MHz）
    // 🔥 V10.0扩展：添加CH2通道同步
    // ⚠️  注意：由于50MHz数据在变化，这种同步方式可能采样到中间值
    // 但对于Bode分析（统计平均）影响较小
    
    // CH1通道同步寄存器
    reg [7:0]   adc_ch1_data_sync1;     // 第一级数据同步
    reg [7:0]   adc_ch1_data_sync2;     // 第二级数据同步（稳定输出）
    reg         adc_ch1_valid_sync1;    // 第一级有效信号同步
    reg         adc_ch1_valid_sync2;    // 第二级有效信号同步（稳定输出）
    
    // CH2通道同步寄存器
    reg [7:0]   adc_ch2_data_sync1;     // 第一级数据同步
    reg [7:0]   adc_ch2_data_sync2;     // 第二级数据同步（稳定输出）
    reg         adc_ch2_valid_sync1;    // 第一级有效信号同步
    reg         adc_ch2_valid_sync2;    // 第二级有效信号同步（稳定输出）
    
    always @(posedge clk125m or negedge reset_n) begin
        if (!reset_n) begin
            // CH1复位
            adc_ch1_data_sync1 <= 8'd0;
            adc_ch1_data_sync2 <= 8'd0;
            adc_ch1_valid_sync1 <= 1'b0;
            adc_ch1_valid_sync2 <= 1'b0;
            // CH2复位
            adc_ch2_data_sync1 <= 8'd0;
            adc_ch2_data_sync2 <= 8'd0;
            adc_ch2_valid_sync1 <= 1'b0;
            adc_ch2_valid_sync2 <= 1'b0;
        end else begin
            // CH1数据同步（2级）
            adc_ch1_data_sync1 <= adc_ch1_data;
            adc_ch1_data_sync2 <= adc_ch1_data_sync1;
            adc_ch1_valid_sync1 <= adc_ch1_valid;
            adc_ch1_valid_sync2 <= adc_ch1_valid_sync1;
            
            // CH2数据同步（2级）
            adc_ch2_data_sync1 <= adc_ch2_data;
            adc_ch2_data_sync2 <= adc_ch2_data_sync1;
            adc_ch2_valid_sync1 <= adc_ch2_valid;
            adc_ch2_valid_sync2 <= adc_ch2_valid_sync1;
        end
    end

    //=========================================================================
    // 系统寄存器（通过命令控制）
    //=========================================================================
    reg [31:0] hex_display;     // 数码管显示值
    reg [31:0] debug_counter;   // 调试计数器

    //=========================================================================
    // 新增：ADC模式控制寄存器
    //=========================================================================
    reg         adc_mode;           // 0=流模式(Stream), 1=Buffer模式
    reg [31:0]  adc_buffer_size;    // Buffer模式采样点数 (默认10000)
    reg         adc_start_cmd;      // 采集启动命令 (单周期脉冲)
    reg         adc_stop_cmd;       // 采集停止命令 (单周期脉冲)
    reg [31:0]  adc_sample_div;     // V3.0: 采样率分频系数 (1=最快12.5MSPS, 2=6.25MSPS, 4=3.125MSPS...)
    reg         ch1_enable;         // 🔥 V5.0: CH1通道使能（硬件级控制）
    reg         ch2_enable;         // 🔥 V5.0: CH2通道使能（硬件级控制）

    // ADC状态寄存器
    wire        adc_busy;           // 采集忙标志
    wire [31:0] adc_captured_count; // 已采集点数
    wire [2:0]  adc_state;          // ADC状态机: 0=空闲, 1=等待触发, 2=采集中, 3=完成
    wire        adc_capture_done;   // Buffer模式：采集完成标志

    //=========================================================================
    // V8.7.1: 统一触发系统（流模式+Buffer模式共用）
    //=========================================================================
    reg         trigger_enable;         // 触发使能（0x22命令）
    reg         trigger_channel;        // 触发通道选择: 0=CH1, 1=CH2
    reg         trigger_edge;           // 触发边沿: 0=上升沿, 1=下降沿
    reg [7:0]   trigger_level;          // 触发电平 (8位ADC值: 0-255)
    // 触发模式说明:
    //   流模式: trigger_enable=0→自动模式(连续采集), =1→正常模式(等待触发)
    //   Buffer模式: 天然单次, trigger_enable控制是否等待触发
    // 注：触发位置功能已实现，trigger_detector会记录触发点位置

    // Buffer模式DDR3接口（连接到buffer_ddr3_writer）
    wire        buffer_ddr3_wr_en;      // DDR3写使能
    wire [127:0] buffer_ddr3_wr_data;   // DDR3写数据（128位，4个采样点对）
    wire        buffer_ddr3_wr_ready;   // DDR3写就绪

    // Buffer模式UDP传输接口（连接到buffer_udp_transmitter）
    wire        buffer_udp_start;       // UDP传输启动
    wire [15:0] buffer_ddr3_rd_data;    // DDR3读数据（16位）
    wire        buffer_ddr3_rd_en;      // DDR3读使能
    wire        buffer_ddr3_rd_valid;   // DDR3读数据有效
    wire        buffer_udp_payload_req; // UDP负载请求
    wire [7:0]  buffer_udp_payload_data;// UDP负载数据
    wire        buffer_udp_tx_done;     // UDP发送完成

    // Buffer模式状态接口（0x2A查询）
    wire [7:0]  buffer_status_byte;     // 状态字节
    wire [31:0] buffer_progress_count;  // 进度计数
    wire        buffer_mode_active;     // Buffer模式激活标志

    //=========================================================================
    // 新增：频率测量相关信号
    //=========================================================================
    reg         freq_measure_request; // 频率测量请求（0x27命令触发）
    reg         freq_cmd_response_pending; // 🔥 新增：等待0x27命令应答完成标志
    wire [31:0] measured_frequency;  // 测得的频率值(Hz)
    wire        freq_valid;          // 频率有效标志
    wire        freq_measuring;      // 频率测量中标志

    // 频率发送控制器信号
    wire [7:0]  freq_tx_data;        // 频率数据发送
    wire        freq_tx_send_en;     // 频率发送使能
    wire        freq_tx_done;        // 频率发送完成
    wire        freq_sending;        // 频率发送中标志
    wire        uart_tx_busy;        // UART发送忙标志（用于频率发送控制）

    // 🔥 新增：自动测频定时器（每1秒触发一次）
    reg [31:0]  auto_freq_timer;     // 自动测频计数器
    reg         auto_freq_trigger;   // 自动测频触发信号（单周期脉冲）
    parameter   AUTO_FREQ_INTERVAL = 32'd50_000_000;  // 1秒 = 50MHz时钟周期

    //=========================================================================
    // 新增：逻辑分析仪控制信号 (来自参考版本 cdc_ch340_logic_FINAL(2))
    //=========================================================================
    // 控制寄存器
    reg         la_capture_en;        // 采集使能（单周期脉冲）
    reg         la_capture_en_delayed;// 🔥 延迟的采集使能（传递给采集模块）
    reg         la_capture_stop;      // 停止采集（单周期脉冲）
    reg [31:0]  la_sample_div;        // 采样分频系数（默认2=25MSPS, 50MHz/2）
    reg [31:0]  la_capture_len;       // 采集长度（0=连续采集）
    reg         la_trigger_en;        // 触发使能
    reg [7:0]   la_trigger_mask;      // 触发掩码
    reg [7:0]   la_trigger_value;     // 触发值

    // 状态信号
    wire [31:0] la_captured_count;    // 已采集字节数
    wire        la_capture_done;      // 采集完成标志
    wire [2:0]  la_state;             // 状态机状态
    wire        la_trigger_detected;  // 触发检测到标志

    // 数据信号（直接连到多路选择器，复用ADC的FIFO）
    wire [7:0]  la_fifo_rd_data;      // 逻辑分析仪数据（来自采集模块）
    wire        la_fifo_wr_en;        // 逻辑分析仪数据有效
    wire        la_fifo_empty;        // 逻辑分析仪空标志（简化逻辑）

    // USB传输控制
    reg         la_usb_enable;        // 逻辑分析仪USB传输使能
    reg         la_fifo_clear;        // FIFO清空信号（在LA启动时清空FIFO）

    // 🔥 新增：看门狗超时保护（防止状态机卡死）
    reg [31:0]  la_watchdog_cnt;      // 看门狗计数器
    reg         la_force_reset;       // 强制复位信号
    localparam  WATCHDOG_TIMEOUT = 32'd250_000_000;  // 5秒超时 (50MHz × 5s)

    // 🔥🔥🔥 V8.1新增：初始化稳定等待（解决冷启动失败）
    reg [15:0]  la_init_wait_cnt;     // 初始化等待计数器
    reg         la_init_done;         // 初始化完成标志
    reg         la_param_stable;      // 参数稳定标志
    localparam  INIT_WAIT_CYCLES = 16'd5000;  // 100us等待 (50MHz × 100us)

    //=========================================================================
    // 新增：DDR3存储相关信号
    //=========================================================================
    // DDR3控制信号
    wire ddr3_init_done;         // DDR3初始化完成
    wire ddr3_wr_load;           // 写通道启动
    wire ddr3_rd_load;           // 读通道启动
    wire ddr3_wr_fifo_full;      // 写FIFO满
    wire ddr3_rd_fifo_empty;     // 读FIFO空
    wire ddr3_rd_fifo_rden;      // 读FIFO读使能
    wire [15:0] ddr3_rd_data;    // 从DDR3读出的16位数据

    // V1.8新增：数据就绪控制标志
    reg  data_ready_flag;        // 数据就绪标志（高电平表示DDR3有足够数据可读）

    // 以太网传输控制信号
    wire [15:0] adc_data_16bit;      // ADC双通道交织后的16位数据
    wire adc_data_16bit_valid;       // 16位数据有效标志
    wire eth_tx_start;                // 以太网发送启动
    wire eth_tx_done;                 // 以太网发送完成
    wire [15:0] eth_packet_count;     // 已发送的UDP包计数

    // DDR3地址配置
    wire [27:0] app_addr_max = 28'd268435455;  // 256MB-1 (地址范围0-255MB)
    wire [7:0] burst_len = 8'd128;              // 突发长度128

    // DDR3控制逻辑
    reg ddr3_wr_load_reg;
    reg ddr3_rd_load_reg;
    reg ddr3_init_done_d1;
    reg adc_stream_active_d1;        // ADC采集状态延迟
    reg [31:0] adc_sample_cnt;       // ADC采样计数器
    reg rd_started;                  // 读取已启动标志
    reg [4:0] wr_load_pulse_cnt;     // wr_load脉冲计数（保持3个时钟周期）
    reg [4:0] rd_load_pulse_cnt;     // rd_load脉冲计数（保持3个时钟周期）
    reg [15:0] done_delay_cnt;       // DONE状态延迟计数器（用于排空FIFO）

    // 数据有效性控制（解决启动随机数问题）
    reg ddr3_data_valid;             // DDR3数据有效标志
    reg [31:0] ddr3_write_count;     // DDR3已写入数据计数（累积）
    reg [31:0] ddr3_read_count;      // DDR3已读取数据计数（累积）
    localparam MIN_WRITE_THRESHOLD = 32'd1000; // 最小写入阈值（写入1000个数据后才允许读取）

    // V4.0新增：DDR3流式传输控制（边写边读流控）
    reg [27:0] ddr3_data_count;      // DDR3当前数据量（字节，实时差值）

    // 流控阈值参数（V3.8：优化低采样率支持）
    localparam DDR3_BUFFER_SIZE       = 28'd268435456;  // 256 MB 总容量
    localparam ALMOST_FULL_THRESHOLD  = 28'd266338304;  // 254 MB (99.2% - 预留2MB)
    localparam ALMOST_EMPTY_THRESHOLD = 28'd32768;      // 32 KB (0.012% - 紧急停止线)
    localparam START_READ_THRESHOLD   = 28'd65536;      // 64 KB (0.024% - 启动传输)
    localparam SAFE_READ_THRESHOLD    = 28'd49152;      // 48 KB (0.018% - 安全继续线)

    // 流控标志
    wire ddr3_almost_full;           // DDR3接近满（需停止写入）
    wire ddr3_almost_empty;          // DDR3接近空（需停止读取）
    wire ddr3_ready_to_start;        // 数据充足，可以启动传输
    wire ddr3_safe_to_continue;      // 数据足够，可以继续读取

    assign ddr3_almost_full      = (ddr3_data_count > ALMOST_FULL_THRESHOLD);
    assign ddr3_almost_empty     = (ddr3_data_count < ALMOST_EMPTY_THRESHOLD);
    assign ddr3_ready_to_start   = (ddr3_data_count >= START_READ_THRESHOLD);
    assign ddr3_safe_to_continue = (ddr3_data_count >= SAFE_READ_THRESHOLD);

    // USB传输状态控制
    reg eth_transfer_active;         // 以太网传输激活标志（用于DDR3流控）

    // FIFO双缓冲锁存（解决跨时钟域和采样问题）
    reg [15:0] adc_data_latched;     // ADC数据锁存
    reg adc_data_valid_latched;      // ADC数据有效锁存

    // DDR3读取三级流水线（参考sequence_playback_serial_v3.v设计）
    // 第一级：锁存读使能和空标志
    reg ddr3_rd_fifo_rden_stage1;
    reg ddr3_rd_fifo_empty_stage1;
    // 第二级：锁存DDR3读出的数据
    reg [15:0] ddr3_rd_data_stage2;
    reg ddr3_rd_data_valid_stage2;
    // 第三级：最终输出数据
    reg [15:0] ddr3_rd_data_stage3;
    reg ddr3_rd_data_valid_stage3;

    //=========================================================================
    // DDS双通道参数信号
    //=========================================================================
    wire [2:0] wave_type_a;     // 通道A波形类型
    wire [31:0] freq_word_a;    // 通道A频率控制字
    wire [8:0] phase_a;         // 通道A相位
    wire [7:0] amplitude_a;     // 通道A幅度
    wire [15:0] duty_cycle_a;   // 通道A占空比（16位精度）
    wire enable_a;              // 通道A使能

    wire [2:0] wave_type_b;     // 通道B波形类型
    wire [31:0] freq_word_b;    // 通道B频率控制字
    wire [8:0] phase_b;         // 通道B相位
    wire [7:0] amplitude_b;     // 通道B幅度
    wire [15:0] duty_cycle_b;   // 通道B占空比（16位精度）
    wire enable_b;              // 通道B使能

    wire [7:0] dds_status;      // DDS状态反馈

    //=========================================================================
    // 序列发生器信号
    //=========================================================================
    wire [7:0] seq_out_internal;    // 8通道序列输出
    wire [7:0] logic_status;        // 序列发生器状态

    //=========================================================================
    // PWM信号
    //=========================================================================
    wire [7:0] pwm_out_internal;    // 8通道PWM输出
    wire pwm_enable_internal;       // PWM使能
    wire [7:0] pwm_status;          // PWM状态

    //=========================================================================
    // 任意波形RAM信号（新增）
    //=========================================================================
    // 写入接口（来自DDS参数控制器）
    wire arb_wr_en_a;           // 通道A RAM写使能
    wire arb_wr_en_b;           // 通道B RAM写使能
    wire [7:0] arb_wr_addr;     // RAM写地址（共用）
    wire [7:0] arb_wr_data;     // RAM写数据（共用）

    // 读取接口（连到DDS模块）
    wire [7:0] arb_rd_addr_a;   // 通道A RAM读地址
    wire [7:0] arb_rd_data_a;   // 通道A RAM读数据
    wire [7:0] arb_rd_addr_b;   // 通道B RAM读地址
    wire [7:0] arb_rd_data_b;   // 通道B RAM读数据

    // 调试接口（新增）
    wire [7:0] debug_first_byte_a;  // 通道A第一个字节（调试用）
    wire [7:0] debug_first_byte_b;  // 通道B第一个字节（调试用）
    wire [7:0] debug_write_count_a; // 通道A写入计数（调试用）
    wire [7:0] debug_write_count_b; // 通道B写入计数（调试用）

    // DAC时钟和数据输出
    wire DA_Clk_internal;
    wire [7:0] DA0_Data_internal;
    wire [7:0] DA1_Data_internal;

    //=========================================================================
    // FX2 USB CDC通信模块（纯净版）
    //=========================================================================
    FX2_CDC_Core fx2_cdc(
                     .clk            (clk_fx2),
                     .reset_n        (~sys_rst),
                     .fx2_fdata      (fx2_fdata),
                     .fx2_flagb      (fx2_flagb),
                     .fx2_flagc      (fx2_flagc),
                     .fx2_ifclk      (fx2_ifclk),
                     .fx2_faddr      (fx2_faddr),
                     .fx2_sloe       (fx2_sloe),
                     .fx2_slwr       (fx2_slwr),
                     .fx2_slrd       (fx2_slrd),
                     .fx2_pkt_end    (fx2_pkt_end),
                     .fx2_slcs       (fx2_slcs),
                     .data_valid     (rx_valid),
                     .fifo_data_in   (rx_data),
                     .fifo_data_out  (tx_data),
                     .fifo_empty     (tx_empty),
                     .fifo_full      (rx_full),
                     .fifordreq      (tx_rd_req),
                     .pkt_end        (pkt_end_sync2)
                 );

    //=========================================================================
    // 接收FIFO: CDC时钟域 -> 系统时钟域
    //=========================================================================
    fifo_in rx_fifo(
                .Data       (rx_data),
                .Reset      (sys_rst),
                .WrClk      (clk_fx2),
                .RdClk      (clk),
                .WrEn       (rx_valid),
                .RdEn       (~rx_empty),
                .Q          (rx_fifo_out),
                .Empty      (rx_empty),
                .Full       (rx_full)
            );

    //=========================================================================
    // UART 应答模块 (CH340)
    //=========================================================================
    parameter UART_BAUD_RATE = 115200;
    parameter CLK_FREQ = 50000000;

    //=========================================================================
    // 命令解析模块 (从USB CDC FIFO读取)
    //=========================================================================
    cdc_cmd_parser cmd_parser(
                       .Clk            (clk),
                       .Reset_n        (~sys_rst),
                       .rx_data        (rx_fifo_out),
                       .rx_valid       (~rx_empty),
                       .cmd            (cmd_code),
                       .length         (cmd_length),
                       .payload_data   (cmd_payload),
                       .payload_valid  (cmd_payload_valid),
                       .cmd_done       (cmd_done),
                       .cmd_error      (cmd_error),
                       .cmd_valid_pulse(cmd_valid_pulse)
                   );

    //=========================================================================
    // 命令处理逻辑
    //=========================================================================
    reg [7:0] cmd_code_reg;
    reg [7:0] cmd_code_latched;  // 锁存命令码（在S_CMD状态锁存）
    reg [15:0] payload_counter;

    // 保存命令码
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst)
            cmd_code_reg <= 8'h0;
        else if (cmd_done)
            cmd_code_reg <= cmd_code;
    end

    // 锁存命令码（在命令码接收时立即锁存）
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst)
            cmd_code_latched <= 8'h0;
        else if (cmd_valid_pulse)
            cmd_code_latched <= cmd_code;
    end

    // Payload计数
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst)
            payload_counter <= 16'h0;
        else if (cmd_error)  // 出错时清零
            payload_counter <= 16'h0;
        else if (spi_cmd_done || i2c_generic_cmd_done || oled_cmd_done || ds18b20_cmd_done)  // ✅ 子模块完成时清零
            payload_counter <= 16'h0;
        else if (cmd_valid_pulse)  // V3.2 关键修复：新命令到来时清零计数器
            payload_counter <= 16'h0;
        else if (cmd_payload_valid)
            payload_counter <= payload_counter + 1;
        // ✅ 修复：不在cmd_done时清零，而是在子模块完成时清零
        // 这样子模块在cmd_valid后仍能看到完整的payload_counter
    end

    //=========================================================================
    // 系统命令处理 (0x00-0x0F) + ADC命令 (0x20-0x26) + Payload数据累积
    //=========================================================================
    reg [31:0] param_buffer;

    //=========================================================================
    //=========================================================================
    // V3.16修复：ADC采样率分频系数独立更新逻辑（解决多次发送失效问题）
    //=========================================================================
    // 问题：param_buffer在cmd_done时被清零，可能导致采样率更新失败
    // 修复：在cmd_valid_pulse时立即锁存分频系数，避免被清零干扰
    reg [31:0] sample_div_latch;  // 分频系数锁存器

    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            adc_sample_div <= 32'd1;  // 复位时恢复默认值
            sample_div_latch <= 32'd1;
        end
        else begin
            // 在接收到0x26命令的payload时，实时锁存分频系数
            if (cmd_code_latched == 8'h26 && cmd_payload_valid) begin
                case (payload_counter)
                    16'h0:
                        sample_div_latch[7:0]   <= cmd_payload;
                    16'h1:
                        sample_div_latch[15:8]  <= cmd_payload;
                    16'h2:
                        sample_div_latch[23:16] <= cmd_payload;
                    16'h3:
                        sample_div_latch[31:24] <= cmd_payload;
                endcase
            end

            // 当0x26命令完成时，使用锁存的值更新分频系数
            if (cmd_done && cmd_valid_flag && cmd_code_latched == 8'h26) begin
                adc_sample_div <= sample_div_latch;
            end
        end
    end

    //=========================================================================
    // 🔥 修复：逻辑分析仪参数独立锁存器（解决0x60/0x61/0x62命令失效问题）
    //=========================================================================
    // 问题：param_buffer在cmd_done时被清零，导致LA参数赋值失败
    // 修复：为LA参数创建独立的锁存器，在payload接收时实时更新
    reg [31:0] la_sample_div_latch;   // 0x60: 采样率分频系数锁存器
    reg [31:0] la_capture_len_latch;  // 0x61: 采集长度锁存器
    reg [23:0] la_trigger_param_latch; // 0x62: 触发参数锁存器 [23:16]=值,[15:8]=掩码,[0]=使能

    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            // 锁存器初始化
            la_sample_div_latch <= 32'd2;
            la_capture_len_latch <= 32'd0;
            la_trigger_param_latch <= 24'h00;

            // 🔥 LA参数寄存器初始化（从主always块迁移，避免多驱动冲突）
            la_sample_div <= 32'd2;      // 默认25MSPS (50MHz/2)
            la_capture_len <= 32'd0;     // 默认连续采集
            la_trigger_en <= 1'b0;       // 默认禁用触发
            la_trigger_mask <= 8'hFF;    // 默认所有通道参与
            la_trigger_value <= 8'h00;   // 默认触发值0
        end
        else begin
            // 0x60: 锁存采样率分频系数
            if (cmd_code_latched == 8'h60 && cmd_payload_valid) begin
                case (payload_counter)
                    16'h0:
                        la_sample_div_latch[7:0]   <= cmd_payload;
                    16'h1:
                        la_sample_div_latch[15:8]  <= cmd_payload;
                    16'h2:
                        la_sample_div_latch[23:16] <= cmd_payload;
                    16'h3:
                        la_sample_div_latch[31:24] <= cmd_payload;
                endcase
            end

            // ⚠️ 0x61锁存逻辑已完全删除
            // 原因：上位机不使用capture_len功能（固定为0连续采集）
            // 避免CDC回环数据污染导致la_capture_len_latch被意外修改
            // 如需恢复，请确保上位机发送完整的0x61命令帧

            // 0x62: 锁存触发参数
            if (cmd_code_latched == 8'h62 && cmd_payload_valid) begin
                case (payload_counter)
                    16'h0:
                        la_trigger_param_latch[0]     <= cmd_payload[0];  // 使能位
                    16'h1:
                        la_trigger_param_latch[15:8]  <= cmd_payload;     // 掩码
                    16'h2:
                        la_trigger_param_latch[23:16] <= cmd_payload;     // 触发值
                endcase
            end

            // 🔥 强制保持la_capture_len_latch为0（防止任何意外修改）
            la_capture_len_latch <= 32'd0;

            // 当LA命令完成时，使用锁存的值更新寄存器
            if (cmd_done && cmd_valid_flag) begin
                case (cmd_code_latched)
                    8'h60: begin
                        la_sample_div <= la_sample_div_latch;
                        // 🔥 V9.1修复：参数稳定标志的清除由主always块统一处理
                        // 避免多驱动冲突（la_param_stable和la_init_wait_cnt在主always块中管理）
                    end
                    // 8'h61: la_capture_len <= la_capture_len_latch;  // ⚠️ 已禁用：上位机未使用，强制保持0（连续采集）
                    8'h62: begin
                        la_trigger_en    <= la_trigger_param_latch[0];
                        la_trigger_mask  <= la_trigger_param_latch[15:8];
                        la_trigger_value <= la_trigger_param_latch[23:16];
                    end
                endcase
            end

            // 🔥🔥🔥 V7.3关键修复：强制保护la_capture_len永远为0
            // 原因：防止CDC回环数据、命令误码等导致la_capture_len被意外修改
            // 后果：如果la_capture_len非0，LA状态机会自动停止，导致采集超时
            // 解决：每个时钟周期强制清零，确保连续采集模式
            la_capture_len <= 32'd0;
        end
    end

    //=========================================================================
    // 🎯 V9.2新增：Bode分析仪参数独立锁存器（0xB0命令14字节参数）
    //=========================================================================
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            bode_freq_start <= 32'd1000;       // 默认1kHz
            bode_freq_stop <= 32'd100000;      // 默认100kHz
            bode_freq_steps <= 16'd100;        // 默认100点
            bode_samples_per_freq <= 32'd1024; // 默认1024采样
            bode_freq_start_latch <= 32'd0;
            bode_freq_stop_latch <= 32'd0;
            bode_freq_steps_latch <= 16'd0;
            bode_samples_latch <= 32'd0;
        end
        else begin
            // 0xB0: 在payload接收时实时锁存参数（14字节）
            if (cmd_code_latched == 8'hB0 && cmd_payload_valid) begin
                case (payload_counter)
                    // Byte 0-3: 起始频率（小端序）
                    16'h0: bode_freq_start_latch[7:0]   <= cmd_payload;
                    16'h1: bode_freq_start_latch[15:8]  <= cmd_payload;
                    16'h2: bode_freq_start_latch[23:16] <= cmd_payload;
                    16'h3: bode_freq_start_latch[31:24] <= cmd_payload;
                    // Byte 4-7: 终止频率（小端序）
                    16'h4: bode_freq_stop_latch[7:0]    <= cmd_payload;
                    16'h5: bode_freq_stop_latch[15:8]   <= cmd_payload;
                    16'h6: bode_freq_stop_latch[23:16]  <= cmd_payload;
                    16'h7: bode_freq_stop_latch[31:24]  <= cmd_payload;
                    // Byte 8-9: 频率点数（小端序）
                    16'h8: bode_freq_steps_latch[7:0]   <= cmd_payload;
                    16'h9: bode_freq_steps_latch[15:8]  <= cmd_payload;
                    // Byte 10-13: 每频点采样数（小端序）
                    16'hA: bode_samples_latch[7:0]      <= cmd_payload;
                    16'hB: bode_samples_latch[15:8]     <= cmd_payload;
                    16'hC: bode_samples_latch[23:16]    <= cmd_payload;
                    16'hD: bode_samples_latch[31:24]    <= cmd_payload;
                    default: ;
                endcase
            end

            // 当0xB0命令完成时，使用锁存的值更新寄存器并产生param_valid脉冲
            if (cmd_done && cmd_valid_flag && cmd_code_latched == 8'hB0) begin
                bode_freq_start <= bode_freq_start_latch;
                bode_freq_stop <= bode_freq_stop_latch;
                bode_freq_steps <= bode_freq_steps_latch;
                bode_samples_per_freq <= bode_samples_latch;
                bode_param_valid <= 1'b1;
            end
            
            // 🎯 Bode命令自动清零（单周期脉冲） - 合并到此always块避免多驱动
            if (bode_param_valid) begin
                bode_param_valid <= 1'b0;
            end
        end
    end

    // 注意: 0x91命令的payload通过FIFO缓冲发送，见蓝牙桥接模块部分

    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            hex_display <= 32'h0;
            param_buffer <= 32'h0;
            bt_enable <= 1'b1;      // 默认使能蓝牙
            bt_baud_rate <= 32'd115200;  // 默认115200波特率

            // 新增：ADC寄存器初始化
            adc_mode <= 1'b0;           // 默认流模式
            adc_buffer_size <= 32'd10000; // 默认10000点

            // V8.7.1: 统一触发配置
            trigger_enable <= 1'b0;     // 默认禁用触发（自动模式）
            trigger_channel <= 1'b0;    // 默认CH1
            trigger_level <= 8'd128;    // 默认中点电平
            trigger_edge <= 1'b0;       // 默认上升沿

            ch1_enable <= 1'b1;         // 🔥 默认CH1使能
            ch2_enable <= 1'b1;         // 🔥 默认CH2使能
            // adc_sample_div 已由独立always块处理（Line 507-528）
            adc_start_cmd <= 1'b0;
            adc_stop_cmd <= 1'b0;
            freq_measure_request <= 1'b0;  // 🔥 V8.8.0修复：初始化频率测量请求标志

            // 🔥 V8.8.0新增：DSA发送总闸初始化（默认关闭，防止上电后误发送）
            dsa_global_tx_enable <= 1'b0;

            // 新增：逻辑分析仪寄存器初始化 (来自参考版本)
            la_capture_en <= 1'b0;
            la_capture_en_delayed <= 1'b0;  // 🔥 延迟启动信号初始化
            la_capture_stop <= 1'b0;
            // ⚠️ LA参数的初始化已移至独立锁存器always块，避免多驱动冲突
            // la_sample_div/la_capture_len/la_trigger_*由Line 645-690统一管理
            la_usb_enable <= 1'b0;       // 默认禁用USB传输
            la_fifo_clear <= 1'b0;       // 默认不清空FIFO
            la_watchdog_cnt <= 32'd0;    // 🔥 看门狗计数器初始化
            la_force_reset <= 1'b0;      // 🔥 强制复位信号初始化
            la_init_wait_cnt <= 16'd0;   // 🔥 初始化计数器
            la_init_done <= 1'b0;        // 🔥 初始化未完成
            la_param_stable <= 1'b0;     // 🔥 参数未稳定
        end
        else begin
            // 🔥🔥🔥 V9.2致命BUG修复：0x60检测必须在计时逻辑之前执行
            // 原因：如果在第4999个周期收到0x60，计时逻辑会将cnt设为5000，
            //       但0x60检测会将cnt复位为0，导致stable永远无法设置为1
            // 解决：将0x60检测提前到计时逻辑之前，保证清零优先级最高
            if (cmd_done && cmd_valid_flag && cmd_code_latched == 8'h60) begin
                la_param_stable <= 1'b0;       // 清除稳定标志
                la_init_wait_cnt <= 16'd0;     // 复位计数器，强制重新等待100us
            end
            // 🔥🔥🔥 V8.1关键修复：统一的初始化等待逻辑（解决冷启动失败+参数稳定性）
            // 1. PLL上电后需要稳定时间（la_init_done）
            // 2. 参数更新后需要同步时间（la_param_stable）
            // 统一使用la_init_wait_cnt避免多驱动冲突
            else if (!la_init_done) begin
                if (pll_lock && la_init_wait_cnt < INIT_WAIT_CYCLES) begin
                    la_init_wait_cnt <= la_init_wait_cnt + 16'd1;
                end
                else if (la_init_wait_cnt >= INIT_WAIT_CYCLES) begin
                    la_init_done <= 1'b1;
                    la_param_stable <= 1'b1;  // 🔥 初始化完成后参数也稳定
                end
            end
            else if (!la_param_stable) begin
                // la_init_done后，如果参数被更新（la_param_stable=0），重新计时100us
                if (la_init_wait_cnt < INIT_WAIT_CYCLES) begin
                    la_init_wait_cnt <= la_init_wait_cnt + 16'd1;
                end
                else begin
                    la_param_stable <= 1'b1;
                    la_init_wait_cnt <= 16'd0;  // 复位计数器供下次使用
                end
            end

            if (cmd_done) begin
                // 只有命令有效时才处理 - 使用锁存后的命令码
                if (cmd_valid_flag) begin
                    case (cmd_code_latched)
                        // 0x00: 系统复位 (软复位，此处略)

                        // 0x03: 设置数码管显示
                        // Payload: [0-3]:显示值(32位,小端序)
                        8'h03: begin
                            hex_display <= param_buffer;  // 使用累积的参数
                        end

                        // 0x05: 蓝牙使能控制
                        // Payload: [0]:使能标志 (0=禁用, 1=使能)
                        8'h05: begin
                            bt_enable <= param_buffer[0];
                        end

                        // 0x90: 蓝牙波特率设置
                        // Payload: [0-3]:波特率值(32位,小端序)
                        8'h90: begin
                            bt_baud_rate <= param_buffer;
                        end

                        //=====================================================
                        // 新增：ADC控制命令 (0x20-0x26)
                        //=====================================================

                        // 0x20: 设置ADC模式
                        8'h20: begin
                            if (cmd_length >= 1) begin
                                adc_mode <= param_buffer[0];
                            end
                        end

                        // 0x21: 设置Buffer大小
                        8'h21: begin
                            if (cmd_length >= 4) begin
                                adc_buffer_size <= param_buffer;
                            end
                        end

                        // 0x22: 设置触发参数 (V8.7.1简化版)
                        // 参数: [使能+通道][边沿][电平]
                        // Byte0: bit0=使能(0=禁用/自动, 1=启用), bit1=通道(0=CH1, 1=CH2)
                        // Byte1: bit0=边沿(0=上升沿, 1=下降沿)
                        // Byte2: 触发电平 (0-255，对应ADC 8位值)
                        8'h22: begin
                            if (cmd_length >= 3) begin
                                trigger_enable  <= param_buffer[0];     // Byte0 bit0: 触发使能
                                trigger_channel <= param_buffer[1];     // Byte0 bit1: 触发通道
                                trigger_edge    <= param_buffer[8];     // Byte1 bit0: 触发边沿
                                trigger_level   <= param_buffer[23:16]; // Byte2: 触发电平
                            end
                        end

                        // 0x23: 启动采集
                        8'h23: begin
                            adc_start_cmd <= 1'b1;
                        end

                        // 0x24: 停止采集
                        8'h24: begin
                            adc_stop_cmd <= 1'b1;
                            hex_display <= 32'h0;  // 清除用户设置显示值
                            // 注: freq_display_valid由频率显示控制always块管理，不在此处清零
                        end

                        // 0x25: 读取ADC状态（预留）
                        8'h25: begin
                            // 状态读取命令，不需要额外处理
                        end

                        // 0x26: 设置采样率分频系数
                        // ✅ V3.17修复: 不再设置hex_display，避免与频率显示跳变
                        // 采样率设置逻辑在独立always块中处理（Line 492-522）
                        8'h26: begin
                            // 采样率分频系数已在独立always块中更新，此处无需额外操作
                            // hex_display保持默认值，由freq_display_valid控制频率显示
                        end

                        // 0x27: 请求频率测量（新增）
                        8'h27: begin
                            freq_measure_request <= 1'b1;
                        end

                        // 0x28: 设置通道使能（硬件级控制）
                        8'h28: begin
                            if (cmd_length >= 2) begin
                                ch1_enable <= param_buffer[7:0] != 8'd0;   // 第1字节：CH1使能
                                ch2_enable <= param_buffer[15:8] != 8'd0;  // 第2字节：CH2使能
                            end
                        end

                        // 0x2A: Buffer模式状态查询
                        8'h2A: begin
                            // 状态通过应答帧返回，不需要额外处理
                        end

                        //=====================================================
                        // 逻辑分析仪命令 (0x60-0x65) - 来自参考版本
                        //=====================================================

                        // 0x60: 设置采样率分频系数（已由独立锁存器处理，见Line 635-690）
                        8'h60: begin
                            // 空处理，实际更新在独立always块中完成
                        end

                        // 0x61: 设置采集长度（已由独立锁存器处理）
                        8'h61: begin
                            // 空处理，实际更新在独立always块中完成
                        end

                        // 0x62: 设置触发参数（已由独立锁存器处理）
                        8'h62: begin
                            // 空处理，实际更新在独立always块中完成
                        end

                        // 0x63: 开始采集
                        8'h63: begin
                            // 🔥🔥🔥 V8.1：检查初始化完成且参数稳定后才允许启动
                            if (la_init_done && la_param_stable) begin
                                la_capture_en <= 1'b1;
                                la_usb_enable <= 1'b1;  // 开始采集时使能USB传输
                                la_fifo_clear <= 1'b1;  // 清空FIFO，避免读到旧数据
                            end
                            // 否则忽略启动命令（静默失败，避免状态机混乱）
                        end

                        // 0x64: 停止采集
                        8'h64: begin
                            la_capture_stop <= 1'b1;
                            la_usb_enable <= 1'b0;    // 立即关闭USB传输
                            la_fifo_clear <= 1'b1;    // 🔥 统一规则：停止时清空FIFO
                        end

                        // 0x65: 读取状态（状态通过应答帧返回）
                        8'h65: begin
                            // 状态读取命令，不需要额外处理
                        end

                        //=====================================================
                        // 数字信号测量命令 (0x66-0x68) - V8.8.0总闸控制
                        //=====================================================
                        // 注：子模块digital_signal_ctrl负责具体逻辑，此处只控制发送总闸

                        // 0x66: 开始8路数字信号测量
                        8'h66: begin
                            dsa_global_tx_enable <= 1'b1;  // 🔥 打开DSA发送总闸
                        end

                        // 0x67: 停止测量
                        8'h67: begin
                            dsa_global_tx_enable <= 1'b0;  // 🔥 强制关闭总闸，截断残留数据流
                        end

                        // 0x68: 读取指定通道测量结果
                        8'h68: begin
                            // 子模块处理，顶层不需要额外操作
                            // 注：0x68会产生dsa_result_valid，但受总闸控制
                        end

                        //=====================================================
                        // 🎯 Bode分析仪命令 (0xB0-0xB3) - V9.2新增
                        //=====================================================
                        
                        // 0xB0: 配置扫频参数 (14字节)
                        // 参数由独立锁存器在payload接收时实时锁存
                        // cmd_done时自动更新并产生param_valid脉冲
                        8'hB0: begin
                            // 参数处理逻辑在独立always块中完成
                        end

                        // 0xB1: 启动扫频
                        8'hB1: begin
                            bode_sweep_enable <= 1'b1;
                        end

                        // 0xB2: 停止扫频
                        8'hB2: begin
                            bode_sweep_stop <= 1'b1;
                        end

                        // 0xB3: 查询扫频状态（预留）
                        8'hB3: begin
                            // 状态查询，上位机解析bode_sweep_active等信号
                        end

                        default:
                            ;
                    endcase
                end

                // 命令处理完成后清空缓冲区（无论有效无效）
                param_buffer <= 32'h0;
            end
            else if (cmd_error) begin
                // 命令错误时也清空缓冲区
                param_buffer <= 32'h0;
            end
            else if (cmd_valid_pulse) begin
                // ✅ V3.3: 新命令开始时清零param_buffer（防止上一条命令残留）
                param_buffer <= 32'h0;
            end
            else if (cmd_payload_valid) begin
                // Payload接收过程中累积数据（小端序）
                case (payload_counter)
                    16'h0:
                        param_buffer[7:0]   <= cmd_payload;
                    16'h1:
                        param_buffer[15:8]  <= cmd_payload;
                    16'h2:
                        param_buffer[23:16] <= cmd_payload;
                    16'h3:
                        param_buffer[31:24] <= cmd_payload;
                    default:
                        ;
                endcase
            end

            // ✅ V3.5 关键修复：将清零逻辑移到else分支内（非复位状态下执行）
            // 这样可以与cmd_done/cmd_error/cmd_payload_valid并行执行，避免优先级冲突

            // ✅ 启动命令自动清零：单周期脉冲，下一个时钟周期立即清零
            if (adc_start_cmd) begin
                adc_start_cmd <= 1'b0;
            end

            // ✅ 停止命令自动清零：单周期脉冲，下一个时钟周期立即清零
            if (adc_stop_cmd) begin
                adc_stop_cmd <= 1'b0;
                // FIFO清空信号会在后续的if块中自动清零
            end

            // 频率测量请求自动清零（单周期脉冲）
            if (freq_measure_request) begin
                freq_measure_request <= 1'b0;
            end

            // 🔥🔥🔥 V8.0 简化修复：直接使用la_capture_en，移除延迟逻辑
            // 原因：延迟逻辑依赖fifo_clear_extended，可能在某些情况下无法正确触发
            // 简化方案：直接使用原始信号，FIFO清空由fifo_clear_extended直接控制写使能
            // 好处：消除延迟链路上的潜在故障点，提高可靠性

            // 生成单周期脉冲
            if (la_capture_en) begin
                la_capture_en_delayed <= 1'b1;
                la_capture_en <= 1'b0;
            end
            else begin
                la_capture_en_delayed <= 1'b0;
            end

            // 逻辑分析仪命令自动清零（单周期脉冲）
            if (la_capture_stop) begin
                la_capture_stop <= 1'b0;
                // 🔥 修复：停止命令不再清除USB使能，让清空逻辑在命令处理时完成
            end

            // 🔥 新增：看门狗超时保护机制（防止状态机卡死）
            // 如果状态机超过5秒没有返回IDLE，强制复位
            if (la_state != 3'd0) begin  // 非IDLE状态
                if (la_watchdog_cnt >= WATCHDOG_TIMEOUT) begin
                    // 超时，强制停止并复位
                    la_force_reset <= 1'b1;
                    la_capture_stop <= 1'b1;
                    la_usb_enable <= 1'b0;
                    la_fifo_clear <= 1'b1;
                    la_watchdog_cnt <= 32'd0;
                end
                else begin
                    la_watchdog_cnt <= la_watchdog_cnt + 32'd1;
                    la_force_reset <= 1'b0;
                end
            end
            else begin
                // IDLE状态，清零看门狗
                la_watchdog_cnt <= 32'd0;
                la_force_reset <= 1'b0;
            end

            // FIFO清空信号自动清零（单周期脉冲）
            if (la_fifo_clear) begin
                la_fifo_clear <= 1'b0;
            end

            // LA采集完成时自动清除USB使能（在数据传输完成后）
            if (la_capture_done) begin
                la_usb_enable <= 1'b0;
            end

            // LA传输完成自动清除USB使能（由fx2时钟域传输完成检测产生）
            if (la_tx_complete_event) begin
                la_usb_enable <= 1'b0;
            end

            // 🎯 Bode扫频命令自动清零（单周期脉冲）
            // 注意：bode_param_valid在参数配置always块中清零，避免多驱动
            if (bode_sweep_enable) begin
                bode_sweep_enable <= 1'b0;
            end
            if (bode_sweep_stop) begin
                bode_sweep_stop <= 1'b0;
            end
        end  // end of else
    end  // end of always

    //=========================================================================
    // 命令有效性检查（组合逻辑，实时检查）
    //=========================================================================
    reg cmd_valid_flag;  // 命令有效标志

    always @(*) begin
        // 实时检查命令码是否有效（组合逻辑）- 使用锁存后的命令码
        case (cmd_code_latched)
            // 系统命令 (0x00-0x0F)
            8'h00,  // 系统复位
            8'h03,  // 设置数码管
            8'h04,  // 读取状态（预留）
            8'h05,  // 蓝牙使能控制

            // DDS命令 (0x10-0x1F)
            8'h10,  // 设置通道A波形
            8'h11,  // 设置通道B波形
            8'h12,  // 设置通道A频率
            8'h13,  // 设置通道B频率
            8'h14,  // 设置通道A相位
            8'h15,  // 设置通道B相位
            8'h16,  // 设置通道A幅度
            8'h17,  // 设置通道B幅度
            8'h18,  // 设置通道使能
            8'h19,  // 批量设置通道A
            8'h1A,  // 批量设置通道B
            8'h1C,  // 设置通道A占空比
            8'h1D,  // 设置通道B占空比
            8'h1E,  // 写入通道A任意波形（新增）
            8'h1F,  // 写入通道B任意波形（新增）

            // ADC命令 (0x20-0x2F)
            8'h20,  // 设置ADC模式
            8'h21,  // 设置Buffer大小
            8'h22,  // 设置触发参数
            8'h23,  // 启动采集
            8'h24,  // 停止采集
            8'h25,  // 读取ADC状态
            8'h26,  // 设置采样率分频系数
            8'h27,  // 请求频率测量（先应答，再返回4字节频率数据）
            8'h28,  // 设置通道使能（🔥 V5.0新增：硬件级控制）
            8'h29,  // Buffer模式触发配置（V8.7.0新增）
            8'h2A,  // Buffer模式状态查询（V8.7.0新增）

            // 序列发生器命令 - 旧协议 (0x30-0x34) 并行+串行共享频率
            8'h30,  // 并行模式配置
            8'h31,  // 串行模式配置
            8'h32,  // 频率控制（全局）
            8'h33,  // 启动输出
            8'h34,  // 停止输出

            // 序列发生器命令 - 新协议 (0x40-0x43) 串行独立频率
            8'h40,  // 配置通道参数（含独立频率）
            8'h41,  // 写入序列数据
            8'h42,  // 使能控制
            8'h43,  // 全局复位

            // PWM命令 (0x50-0x52)
            8'h50,  // PWM配置
            8'h51,  // PWM使能
            8'h52,  // PWM停止

            // 逻辑分析仪命令 (0x60-0x65)
            8'h60,  // 设置采样率
            8'h61,  // 设置采集长度
            8'h62,  // 设置触发参数
            8'h63,  // 开始采集
            8'h64,  // 停止采集
            8'h65,  // 读取状态

            // 数字信号测量命令 (0x66-0x68)
            8'h66,  // 开始8路测量
            8'h67,  // 停止测量
            8'h68,  // 读取指定通道结果

            // I2C命令 (0x70-0x76)
            8'h70,  // 通用I2C主机写入
            // 0x71, 0x72 保留(读取/扫描功能已移除)
            8'h73,  // OLED初始化
            8'h74,  // OLED清屏
            8'h75,  // OLED全亮显示
            8'h76,  // OLED显示文本(使用内置字库)

            // SPI命令 (0x80-0x87)
            8'h80,  // SPI配置
            8'h81,  // SPI传输
            8'h82,  // Flash读ID
            8'h83,  // Flash读取
            8'h84,  // Flash写入
            8'h85,  // Flash扇区擦除
            8'h86,  // Flash全片擦除
            8'h87,  // Flash读状态

            // UART/蓝牙命令 (0x90-0x92)
            8'h90,  // 蓝牙波特率设置
            8'h91,  // UART发送数据到蓝牙

            // DS18B20命令 (0xA0-0xA2)
            8'hA0,  // 单次读取温度
            8'hA1,  // 开始连续监控
            8'hA2,  // 停止连续监控

            // CAN总线命令 (0xC0-0xC4)
            8'hC0,  // CAN配置波特率
            8'hC1,  // CAN发送帧
            8'hC2,  // CAN设置过滤器
            8'hC3,  // CAN读取状态
            8'hC4,  // CAN接收数据上报

            // Bode分析仪命令 (0xB0-0xB3)
            8'hB0,  // 配置扫频参数
            8'hB1,  // 启动扫频
            8'hB2,  // 停止扫频
            8'hB3:  // 查询扫频状态
                cmd_valid_flag = 1'b1;

            default:
                cmd_valid_flag = 1'b0;  // 无效命令
        endcase
    end

    //=========================================================================
    // 应答生成逻辑 - 修改为触发UART发送(单周期脉冲)
    //=========================================================================
    reg cmd_done_d;   // cmd_done 延迟一拍用于边沿检测
    reg cmd_error_d;  // cmd_error 延迟一拍用于边沿检测

    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            cmd_done_d <= 1'b0;
            cmd_error_d <= 1'b0;
        end
        else begin
            cmd_done_d <= cmd_done;
            cmd_error_d <= cmd_error;
        end
    end

    wire cmd_done_posedge;
    wire cmd_error_posedge;
    assign cmd_done_posedge = cmd_done && (!cmd_done_d);     // cmd_done 上升沿
    assign cmd_error_posedge = cmd_error && (!cmd_error_d);  // cmd_error 上升沿

    // 命令完成或错误时都要生成应答（包括OLED、通用I2C和SPI）
    // 命令完成信号组合：
    // - 简单命令：cmd_done 后一周期检查（等待 cmd_code_reg 更新）
    // - 子模块命令：等待子模块完成信号
    reg cmd_done_posedge_d;  // cmd_done_posedge 延迟一拍
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst)
            cmd_done_posedge_d <= 1'b0;
        else
            cmd_done_posedge_d <= cmd_done_posedge;
    end

    wire is_submodule_cmd;  // 判断是否是子模块命令（使用已更新的 cmd_code_reg）
    assign is_submodule_cmd = (cmd_code_reg >= 8'h66 && cmd_code_reg <= 8'h68) ||  // 🔥修复：DSA数字信号测量
           (cmd_code_reg >= 8'h70 && cmd_code_reg <= 8'h76) ||  // I2C/OLED
           (cmd_code_reg >= 8'h80 && cmd_code_reg <= 8'h87) ||  // SPI
           (cmd_code_reg >= 8'hA0 && cmd_code_reg <= 8'hA2) ||  // DS18B20
           (cmd_code_reg >= 8'hC0 && cmd_code_reg <= 8'hC4);    // CAN总线
    // 注意：0x27频率测量不是子模块命令，因为它是简单命令（立即返回应答帧）
    // 注意：DSA (0x66-0x68)是子模块命令，由digital_signal_ctrl自己发送应答帧+数据

    wire simple_cmd_finish;  // 简单命令完成（延迟一周期，确保 cmd_code_reg 已更新）
    assign simple_cmd_finish = cmd_done_posedge_d && !is_submodule_cmd;

    // 🔥 V8.8.0关键修复：移除dsa_cmd_done，避免干扰其他命令的应答帧
    // DSA命令(0x66/0x67/0x68)不使用统一应答帧机制，而是由状态机直接发送数据
    // 如果保留dsa_cmd_done，会导致0x27等简单命令的应答帧生成时序混乱
    wire cmd_finish;
    assign cmd_finish = simple_cmd_finish || cmd_error_posedge || oled_cmd_done || i2c_generic_cmd_done || spi_cmd_done || ds18b20_cmd_done || can_response_valid;

    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            response_valid <= 1'b0;
            response_mod_id <= 8'h0;
            response_func_id <= 8'h0;
            response_status <= 8'h0;
        end
        else if (cmd_finish) begin
            // 命令解析完成或出错，触发UART应答(单周期脉冲)
            response_valid <= 1'b1;

            // CAN模块有独立的应答信息
            if (can_response_valid) begin
                response_mod_id <= can_resp_mod_id;
                response_func_id <= can_resp_func_id;
                response_status <= can_resp_status;
            end
            else begin
                response_mod_id <= 8'h01;       // 固定为系统模块(后续可扩展)
                response_func_id <= cmd_code_latched;

                // 状态码生成：
                // 0x00 = 成功
                // 0x01 = 校验错误
                // 0x02 = 无效命令
                if (cmd_error_posedge)
                    response_status <= 8'h01;  // 校验错误
                else if (!cmd_valid_flag)
                    response_status <= 8'h02;  // 无效命令
                else
                    response_status <= 8'h00;  // 成功
            end
        end
        else begin
            // 下一周期自动清零,形成单周期脉冲
            response_valid <= 1'b0;
        end
    end

    //=========================================================================
    // 8路数字信号分析控制器（新增 V2.3）- 改用50MHz简化设计
    // 命令: 0x66=开始测量, 0x67=停止测量, 0x68=读取结果
    //=========================================================================
    wire [2:0] dsa_result_channel;
    wire [31:0] dsa_result_freq;
    wire [31:0] dsa_result_high_cycles;
    wire [31:0] dsa_result_low_cycles;
    wire dsa_result_valid;
    wire dsa_cmd_done;  // 🔥 改用cmd_done，参照DS18B20
    wire dsa_measuring; // 🔥 V8.8.0新增：DSA测量中标志

    digital_signal_ctrl u_digital_signal_ctrl(
                            .clk            (clk),              // 使用50MHz时钟
                            .rst_n          (reset_n),
                            .signal_in      (LOGIC_IN),         // 8路逻辑输入
                            .cmd            (cmd_code),
                            .payload_data   (cmd_payload),
                            .payload_valid  (cmd_payload_valid),
                            .cmd_done       (cmd_done),
                            .cmd_valid_pulse(cmd_valid_pulse),
                            .result_channel (dsa_result_channel),
                            .result_freq    (dsa_result_freq),
                            .result_high_cycles(dsa_result_high_cycles),
                            .result_low_cycles(dsa_result_low_cycles),
                            .result_valid   (dsa_result_valid),
                            .cmd_done_out   (dsa_cmd_done),     // 🔥 新增：输出cmd_done信号
                            .dsa_measuring  (dsa_measuring)     // 🔥 V8.8.0新增：测量状态输出
                        );

    //=========================================================================
    // Bode分析仪模块 — 未实现（IP核文件缺失），输出全部接零
    //=========================================================================
    assign bode_iq_valid       = 1'b0;
    assign bode_demod_enable   = 1'b0;

    assign bode_uart_tx_data   = 8'd0;
    assign bode_uart_tx_send_en = 1'b0;
    assign bode_dds_freq_word  = 32'd0;
    assign bode_dds_phase      = 9'd0;
    assign bode_dds_amplitude  = 8'd0;
    assign bode_dds_enable     = 1'b0;
    assign bode_formatter_busy = 1'b0;
    assign bode_uart_tx_send_active = 1'b0;
    assign bode_formatter_state = 3'd0;
    assign bode_sweep_active   = 1'b0;
    assign bode_data_ready     = 1'b0;
    assign bode_current_index  = 16'd0;
    assign bode_current_freq   = 32'd0;

    // 原例化（Bode_Analyzer IP 缺失，暂时移除）:
    // bode_analyzer_top u_bode_analyzer( ... );

    // DSA数据发送握手 - 移除旧的assign，由状态机管理

    //=========================================================================
    // UART发送多路复用模块 (频率 > DSA > SPI > DS18B20 > 应答帧 > 蓝牙透传)
    // 🔥 V8.8.0优先级调整：频率数据提升至最高优先级，避免被DSA数据阻塞导致3秒超时
    //=========================================================================
    uart_tx_mux u_uart_tx_mux(
                    .clk            (clk),
                    .rst_n          (reset_n),
                    // CAN接收数据通道(第2优先级 - CAN接收帧上报)
                    .can_rx_data    (can_rx_report_data),
                    .can_rx_send_en (can_rx_report_valid),
                    .can_rx_tx_done (can_rx_report_ready_internal),
                    // Bode分析仪数据通道(第3优先级 - Bode扫频数据)
                    .bode_data      (bode_uart_tx_data),
                    .bode_send_en   (bode_uart_tx_send_en),
                    .bode_tx_done   (bode_uart_tx_done),
                    // DSA数据通道(第4优先级 - 数字信号测量数据)
                    .dsa_data       (dsa_data_byte),        // 使用状态机发送的字节
                    .dsa_send_en    (dsa_data_tx_en),       // 使用状态机控制的使能
                    .dsa_tx_done    (dsa_data_uart_done),   // UART发送完成信号
                    // SPI数据通道(第4优先级 - Flash读取数据)
                    .spi_data       (spi_data_byte),
                    .spi_send_en    (spi_data_tx_en),
                    .spi_tx_done    (spi_data_uart_done),
                    // DS18B20数据通道(第5优先级 - 温度数据)
                    .ds18b20_data   (ds18b20_data_byte),
                    .ds18b20_send_en(ds18b20_data_tx_en),
                    .ds18b20_tx_done(ds18b20_data_uart_done),
                    // 频率数据通道(最高优先级 - 频率测量数据)
                    .freq_data      (freq_tx_data),
                    .freq_send_en   (freq_tx_send_en),
                    .freq_tx_done   (freq_tx_done),
                    // 应答帧通道(第6优先级)
                    .resp_data      (uart_tx_data),
                    .resp_send_en   (uart_tx_send_en),
                    .resp_tx_done   (resp_tx_done_mux),
                    // 蓝牙透传通道(低优先级)
                    .bt_data        (bt_tx_data_ch),
                    .bt_send_en     (bt_tx_send_en_ch),
                    .bt_tx_done     (bt_tx_done_mux),
                    // UART底层发送
                    .uart_tx_data   (uart_tx_data_mux),
                    .uart_tx_send_en(uart_tx_send_en_mux),
                    .uart_tx_done   (uart_tx_done_internal),
                    // 调试
                    .current_channel(uart_mux_channel)
                );

    //=========================================================================
    // UART 应答模块例化 (连接到多路复用器)
    //=========================================================================
    uart_response_tx u_uart_response_tx(
                         .clk            (clk),
                         .rst_n          (reset_n),
                         .response_valid (response_valid),
                         .mod_id         (response_mod_id),
                         .func_id        (response_func_id),
                         .status         (response_status),
                         .data           (i2c_generic_response),  // I2C读取/扫描结果
                         .response_done  (response_done_uart),
                         .tx_data        (uart_tx_data),
                         .tx_send_en     (uart_tx_send_en),
                         .tx_done        (resp_tx_done_mux)  // 从MUX获取完成信号
                     );

    //=========================================================================
    // UART底层发送模块 (连接到多路复用器)
    //=========================================================================
    wire uart_state;  // ✅ 新增：UART发送状态信号（1=忙，0=空闲）
    
    uart_byte_tx u_uart_byte_tx(
                     .Clk        (clk),
                     .Rst_n      (reset_n),
                     .data_byte  (uart_tx_data_mux),
                     .send_en    (uart_tx_send_en_mux),
                     .Baud_Rate  (UART_BAUD_RATE),
                     .Clk_Freq   (CLK_FREQ),
                     .uart_tx    (uart_tx),
                     .Tx_Done    (uart_tx_done_internal),
                     .uart_state (uart_state)  // ✅ 修复：连接uart_state信号
                 );

    //=========================================================================
    // 蓝牙串口透传桥接模块
    // 蓝牙模块预配置为115200波特率
    // 接收0x91命令的payload并发送到蓝牙
    // 增加FIFO缓冲避免快速连续payload丢失
    //=========================================================================

    // UART发送FIFO缓冲 (16字节FIFO)
    reg [7:0] bt_fifo [0:15];
    reg [3:0] bt_wr_ptr;
    reg [3:0] bt_rd_ptr;
    wire bt_fifo_empty;
    wire bt_fifo_full;

    assign bt_fifo_empty = (bt_wr_ptr == bt_rd_ptr);
    assign bt_fifo_full = ((bt_wr_ptr + 4'd1) == bt_rd_ptr);

    // 写入FIFO (当收到0x91的payload时)
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            bt_wr_ptr <= 4'h0;
        end
        else if (cmd_payload_valid && cmd_code_latched == 8'h91 && !bt_fifo_full) begin
            bt_fifo[bt_wr_ptr] <= cmd_payload;
            bt_wr_ptr <= bt_wr_ptr + 4'd1;
        end
    end

    // 读取FIFO并触发蓝牙发送
    reg bt_tx_trigger;
    reg [7:0] bt_tx_data_buf;
    reg [1:0] bt_send_state;
    wire bt_cdc_done;  // bt_uart_bridge发送完成信号

    localparam BT_IDLE = 2'd0;
    localparam BT_TRIG = 2'd1;
    localparam BT_WAIT = 2'd2;

    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            bt_rd_ptr <= 4'h0;
            bt_tx_trigger <= 1'b0;
            bt_tx_data_buf <= 8'h0;
            bt_send_state <= BT_IDLE;
        end
        else begin
            case (bt_send_state)
                BT_IDLE: begin
                    bt_tx_trigger <= 1'b0;
                    // 如果FIFO非空，取出一个字节
                    if (!bt_fifo_empty) begin
                        bt_tx_data_buf <= bt_fifo[bt_rd_ptr];
                        bt_rd_ptr <= bt_rd_ptr + 4'd1;
                        bt_send_state <= BT_TRIG;
                    end
                end

                BT_TRIG: begin
                    // 产生一个周期的触发脉冲
                    bt_tx_trigger <= 1'b1;
                    bt_send_state <= BT_WAIT;
                end

                BT_WAIT: begin
                    // 等待bt_uart_bridge完成发送（参考uart_data_tx的做法）
                    bt_tx_trigger <= 1'b0;
                    if (bt_cdc_done) begin
                        bt_send_state <= BT_IDLE;  // 完成后返回IDLE，准备发送下一个字节
                    end
                end

                default:
                    bt_send_state <= BT_IDLE;
            endcase
        end
    end

    bt_uart_bridge u_bt_bridge(
                       .clk            (clk),
                       .rst_n          (reset_n),
                       // CDC侧接口（从FIFO缓冲接收）
                       .cdc_rx_data    (bt_tx_data_buf),
                       .cdc_rx_valid   (bt_tx_trigger),
                       .cdc_tx_req     (),  // 暂不使用
                       .cdc_tx_done    (bt_cdc_done),  // 发送完成信号
                       // 蓝牙串口
                       .bt_rx          (bt_rx),
                       .bt_tx          (bt_tx),
                       // CH340串口(透传通道,连接到MUX)
                       .uart_rx        (uart_rx),
                       .uart_tx_data   (bt_tx_data_ch),
                       .uart_tx_send_en(bt_tx_send_en_ch),
                       .uart_tx_done   (bt_tx_done_mux),
                       .uart_tx_busy   (uart_mux_channel != 3'd0),  // 任意通道活动认为忙（IDLE=0）
                       // 控制信号
                       .bt_enable      (bt_enable),
                       .baud_rate_cfg  (bt_baud_rate),  // 波特率配置
                       // 状态
                       .status         (bt_status)
                   );    //=========================================================================
    // 发送FIFO: 系统时钟域 -> CDC时钟域 (已废弃,通过UART应答)
    //=========================================================================
    /*
    // 注释原USB应答FIFO相关代码
    always @(posedge clk_fx2 or posedge sys_rst) begin
        if (sys_rst) begin
            pkt_end_sync1 <= 1'b1;
            pkt_end_sync2 <= 1'b1;
        end
        else begin
            pkt_end_sync1 <= pkt_end;
            pkt_end_sync2 <= pkt_end_sync1;
        end
    end

    assign pkt_end = (resp_state == RESP_DONE);

    fifo_top tx_fifo(
                 .Data       (tx_data),
                 .Reset      (sys_rst | (!fx2_pkt_end)),
                 .WrClk      (clk),
                 .RdClk      (clk_fx2),
                 .WrEn       (tx_valid),
                 .RdEn       (tx_rd_req),
                 .Q          (tx_fifo_out),
                 .Almost_Empty(tx_empty),
                 .Empty      (),
                 .Full       (tx_full)
             );
    */

    //=========================================================================
    //=========================================================================
    // LED状态指示（逻辑分析仪优先，ADC/Buffer次之）
    //=========================================================================
    reg [25:0] led_cnt;
    always @(posedge clk) led_cnt <= led_cnt + 1;

    // LED状态指示（精简版）
    // 删除逻辑分析仪调试信息，只保留核心ADC和DDR3状态指示

    // LED[0] - 心跳灯（系统运行指示）
    assign led[0] = led_cnt[24];  // 约1.5秒闪烁周期（50MHz/2^25≈1.5Hz）

    // LED[1] - DDR3初始化状态
    assign led[1] = ddr3_init_done;

    // LED[2] - 🔥 Bode扫频进行中（V9.2新增调试指示）
    assign led[2] = bode_sweep_active;

    // LED[3] - 🔥 IQ解调输出有效（V9.2.4 关键调试！）
    // 闪烁表示CIC滤波器正在输出IQ数据（97.656kHz脉冲）
    // 如果LED3不亮：CDC同步失败或CIC未输出
    assign led[3] = bode_iq_valid;  // 直接显示iq_valid信号

    // LED[4] - 🔥 ADC CH1采集状态（V9.2调试指示）
    assign led[4] = adc_ch1_stream_active;

    // LED[5] - 🔥 V9.2.17临时诊断：formatter busy状态
    // 如果一直亮：formatter卡在发送状态
    // 如果规律闪烁：formatter正常工作
    assign led[5] = bode_formatter_busy;

    // LED[6] - 🔥 V9.2.17临时诊断：uart_tx_busy状态
    // 如果一直亮：uart_tx_busy=1，UART底层卡死
    // 如果不亮：uart_tx_busy=0，UART空闲正常
    assign led[6] = uart_tx_busy;  // 直接观察uart_state

    // LED[7] - 🔥 V9.2.17临时诊断：UART发送请求
    // 如果闪烁：formatter在发送字节
    // 如果不亮：formatter没有发送请求
    assign led[7] = bode_uart_tx_send_active;  // UART发送使能信号

    //=========================================================================
    // 数码管显示
    //=========================================================================
    wire [7:0] hex_sel;
    wire [7:0] hex_seg;  // 8位：[7]=DP小数点, [6:0]=7段码

    // 直接显示hex_display寄存器（由命令0x03控制）
    // 不再使用debug_counter自动累加

    // 调试计数器（统计收到的命令数，仅用于调试，不显示）
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst)
            debug_counter <= 32'h0;
        else if (cmd_done)
            debug_counter <= debug_counter + 1;
    end

    // 🔍 DS18B20温度显示控制
    reg [15:0] ds18b20_temp_display;
    reg ds18b20_reading_active;  // DS18B20读取活动标志

    // 检测DS18B20读取活动状态（cmd_code = 0xA0时开始，0xA2时停止）
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            ds18b20_reading_active <= 1'b0;
        end
        else if (ds18b20_cmd_done) begin
            if (ds18b20_cmd_code_reg == 8'hA0)
                ds18b20_reading_active <= 1'b1;  // 开始读取
            else if (ds18b20_cmd_code_reg == 8'hA2)
                ds18b20_reading_active <= 1'b0;  // 停止读取
        end
    end

    // 温度数据缓存（当温度有效时更新）
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst)
            ds18b20_temp_display <= 16'h0;
        else if (ds18b20_temp_valid)
            ds18b20_temp_display <= ds18b20_temp_data;
    end

    // DS18B20温度BCD转换（参考ctrl.v实现）
    wire [23:0] temp_bcd;
    ds18b20_temp_display temp_conv (
                             .temp_raw(ds18b20_temp_display),
                             .precision(2'b11),  // 固定12位精度
                             .bcd_data(temp_bcd)
                         );

    // 数码管显示值选择
    // 优先级修改：频率测量值(BCD) > DS18B20温度 > 默认显示
    // 注意：去除16进制用户设置显示，避免与频率显示跳变
    wire [31:0] hex_display_final;
    wire [7:0] hex_sel_mask;  // 位选掩码，控制哪些数码管亮
    wire [7:0] hex_dot_mask;  // 小数点掩码，控制哪些位显示小数点

    // 默认显示 "HI FP6A" (从右到左：位0=A, 位1=6, 位2=P, 位3=F, 位4=I, 位5=H, 位6-7=不亮)
    // 字符编码：A=0xA, 6=0x6, P无标准编码需扩展, F=0xF, I=1(0x1), H无标准编码需扩展
    // 临时使用近似：H用B(0xB), P用C(0xC)

    // 🔥 双通道频率显示控制（每2秒切换一次）
    reg [31:0] channel_switch_cnt;
    reg freq_display_channel;  // 0=CH1, 1=CH2
    localparam CHANNEL_SWITCH_INTERVAL = 32'd100_000_000;  // 2秒切换间隔(50MHz)

    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            channel_switch_cnt <= 32'd0;
            freq_display_channel <= 1'b0;
        end
        else if (!adc_stream_active) begin
            // 非采集状态：重置为CH1
            channel_switch_cnt <= 32'd0;
            freq_display_channel <= 1'b0;
        end
        else begin
            // 采集状态：每2秒切换通道
            if (channel_switch_cnt >= CHANNEL_SWITCH_INTERVAL - 1) begin
                channel_switch_cnt <= 32'd0;
                freq_display_channel <= ~freq_display_channel;
            end
            else begin
                channel_switch_cnt <= channel_switch_cnt + 32'd1;
            end
        end
    end

    // 根据通道选择器选择显示的频率
    wire [31:0] freq_display_bcd;
    wire freq_display_active;
    assign freq_display_bcd = freq_display_channel ? measured_freq_bcd_ch2 : measured_freq_bcd_ch1;
    assign freq_display_active = freq_display_channel ? freq_display_valid_ch2 : freq_display_valid_ch1;

    // 🔥 V5.10优化：数码管显示逻辑（简化版，删除LA调试信息）
    // 优先级：DS18B20温度 > ADC采集状态(显示频率) > 频率显示 > 默认显示

    assign hex_display_final = (ds18b20_reading_active) ? {8'h00, temp_bcd} :  // DS18B20读取时显示温度BCD码（6位）
           (adc_stream_active || eth_transfer_active) ? freq_display_bcd :  // ADC采集中显示频率
           (freq_display_active) ? freq_display_bcd :  // 频率测量完成：显示选中通道的频率值
           32'h00B1_FC6A;  // 默认显示 "hifpga"

    assign hex_sel_mask = (ds18b20_reading_active) ? 8'b0011_1111 :  // DS18B20：右侧6位显示温度
           (adc_stream_active || eth_transfer_active) ? 8'b1111_1111 :  // 采集中全8位显示频率
           (freq_display_active) ? 8'b1111_1111 :  // 频率：全部显示
           8'b0011_1111;  // 默认：右侧6位显示 "hifpga"

    // 🔥 小数点掩码：简化版（删除LA模式）
    // 温度格式：符号 十位 个位.小数1 小数2 小数3 (bit3小数点)
    // 频率格式：CH1无小数点, CH2在最高位显示小数点(bit7)标识CH2
    assign hex_dot_mask = (ds18b20_reading_active) ? 8'b0000_1000 :   // DS18B20温度
           (freq_display_active && freq_display_channel) ? 8'b1000_0000 :  // CH2频率显示：bit7小数点标识
           8'b0000_0000;  // CH1频率或默认：无小数点

    // 数码管驱动
    hc595_driver hc595_drv(
                     .clk        (clk),
                     .reset_n    (reset_n),
                     .data       ({1'b1, hex_seg, hex_sel}),
                     .s_en       (1'b1),
                     .sh_cp      (sh_cp),
                     .st_cp      (st_cp),
                     .ds         (ds)
                 );

    hex8_ext hex8_inst(
                 .clk        (clk),
                 .reset_n    (reset_n),
                 .en         (1'b1),
                 .disp_data  (hex_display_final),  // 显示选择后的值
                 .sel_mask   (hex_sel_mask),       // 位选掩码
                 .dot_mask   (hex_dot_mask),       // 小数点掩码
                 .sel        (hex_sel),
                 .seg        (hex_seg)
             );

    //=========================================================================
    // DDS参数控制器（处理0x10-0x1A命令）
    //=========================================================================
    DDS_Param_Controller dds_param_ctrl(
                             .Clk            (clk),
                             .Rst_n          (~sys_rst),
                             .cmd            (cmd_code),
                             .payload_data   (cmd_payload),
                             .payload_valid  (cmd_payload_valid),
                             .cmd_done       (cmd_done),
                             .wave_type_a    (wave_type_a),
                             .freq_word_a    (freq_word_a),
                             .phase_a        (phase_a),
                             .amplitude_a    (amplitude_a),
                             .duty_cycle_a   (duty_cycle_a),
                             .enable_a       (enable_a),
                             .wave_type_b    (wave_type_b),
                             .freq_word_b    (freq_word_b),
                             .phase_b        (phase_b),
                             .amplitude_b    (amplitude_b),
                             .duty_cycle_b   (duty_cycle_b),
                             .enable_b       (enable_b),
                             .status         (dds_status),
                             // 任意波形RAM写接口（新增）
                             .arb_wr_en_a    (arb_wr_en_a),
                             .arb_wr_en_b    (arb_wr_en_b),
                             .arb_wr_addr    (arb_wr_addr),
                             .arb_wr_data    (arb_wr_data)
                         );

    //=========================================================================
    // 任意波形RAM模块（带调试接口的V2版本）
    //=========================================================================
    arb_wave_ram_simple u_arb_ram(
                            .Clk        (clk),              // 写时钟：50MHz系统时钟
                            .Clk_DDS    (clk125m),          // 读时钟：125MHz DDS时钟
                            .Rst_n      (~sys_rst),
                            // 写接口（来自DDS参数控制器）
                            .wr_en_a    (arb_wr_en_a),
                            .wr_en_b    (arb_wr_en_b),
                            .wr_addr    (arb_wr_addr),
                            .wr_data    (arb_wr_data),
                            // 读接口 - 通道A（连到DDS模块）
                            .rd_addr_a  (arb_rd_addr_a),
                            .rd_data_a  (arb_rd_data_a),
                            // 读接口 - 通道B（连到DDS模块）
                            .rd_addr_b  (arb_rd_addr_b),
                            .rd_data_b  (arb_rd_data_b),
                            // 调试接口（连到LED显示）
                            .debug_first_byte_a  (debug_first_byte_a),
                            .debug_first_byte_b  (debug_first_byte_b),
                            .debug_write_count_a (debug_write_count_a),
                            .debug_write_count_b (debug_write_count_b)
                        );

    //=========================================================================
    // 🔥 V9.2新增：DDS通道A多路复用逻辑（Bode扫频优先）
    //=========================================================================
    // 当Bode分析仪激活时，接管DDS通道A输出扫频激励信号
    // 否则使用用户通过函数发生器命令（0x10-0x1F）配置的参数
    
    wire [31:0] freq_word_a_mux;
    wire [8:0]  phase_a_mux;
    wire [7:0]  amplitude_a_mux;
    wire        enable_a_mux;
    wire [2:0]  wave_type_a_mux;
    
    assign freq_word_a_mux  = bode_dds_enable ? bode_dds_freq_word : freq_word_a;
    assign phase_a_mux      = bode_dds_enable ? bode_dds_phase     : phase_a;
    assign amplitude_a_mux  = bode_dds_enable ? bode_dds_amplitude : amplitude_a;
    assign enable_a_mux     = bode_dds_enable ? 1'b1               : enable_a;
    assign wave_type_a_mux  = bode_dds_enable ? 3'd0               : wave_type_a;  // Bode强制正弦波

    //=========================================================================
    // 双通道DDS模块
    //=========================================================================
    DDS_Module_Dual dds_dual_inst(
                        .Clk            (clk125m),
                        .Rst_n          (~sys_rst),
                        .EN             (1'b1),
                        .wave_type_a    (wave_type_a_mux),            // 🔥 Bode模式下强制正弦波
                        .freq_word_a    (freq_word_a_mux),            // 🔥 Bode模式下使用扫频频率
                        .phase_a        (phase_a_mux),                // 🔥 Bode模式下使用0度相位
                        .amplitude_a    (enable_a_mux ? amplitude_a_mux : 8'd0),  // 🔥 Bode模式下使用80%幅度
                        .duty_cycle_a   (duty_cycle_a),
                        .wave_type_b    (wave_type_b),
                        .freq_word_b    (freq_word_b),
                        .phase_b        (phase_b),
                        .amplitude_b    (enable_b ? amplitude_b : 8'd0),
                        .duty_cycle_b   (duty_cycle_b),
                        // 任意波形RAM读接口（新增）
                        .arb_rd_addr_a  (arb_rd_addr_a),
                        .arb_rd_data_a  (arb_rd_data_a),
                        .arb_rd_addr_b  (arb_rd_addr_b),
                        .arb_rd_data_b  (arb_rd_data_b),
                        .DA_Clk         (DA_Clk_internal),
                        .DA0_Data       (DA0_Data_internal),
                        .DA1_Data       (DA1_Data_internal)
                    );

    //=========================================================================
    // PWM参数控制器（新增 V1.0）
    // 处理命令: 0x50-0x52 (PWM配置/使能/停止)
    // 模块路径: src/LogicAnalyzer/
    // 复用策略: 参数解析参考sequence_param_controller, 生成逻辑参考DDS方波
    //=========================================================================
    pwm_param_controller pwm_ctrl_inst(
                             .clk            (clk),
                             .rst_n          (~sys_rst),
                             .cmd            (cmd_code),
                             .payload_data   (cmd_payload),
                             .payload_valid  (cmd_payload_valid),
                             .cmd_done       (cmd_done),
                             .pwm_output     (pwm_out_internal),
                             .pwm_enable     (pwm_enable_internal),
                             .status         (pwm_status)
                         );
    //=========================================================================
    // 序列发生器模块（新架构 V1.0）
    // 处理命令:
    //   0x30-0x34: 序列发生器旧协议（并行+串行共享频率）
    //   0x40-0x43: 序列发生器新协议（串行独立频率）
    // 模块路径: src/LogicAnalyzer/
    //=========================================================================
    logic_analyzer_top logic_analyzer_inst(
                           .clk            (clk),           // 系统时钟 50MHz
                           .clk_sample     (clk125m),       // 采样时钟 125MHz
                           .rst_n          (~sys_rst),
                           .cmd            (cmd_code),
                           .payload_data   (cmd_payload),
                           .payload_valid  (cmd_payload_valid),
                           .cmd_done       (cmd_done),
                           .logic_in       (8'h00),         // 逻辑输入（暂时悬空）
                           .logic_out      (seq_out_internal),
                           .status         (logic_status)
                       );

    //=========================================================================
    // DAC输出连接
    //=========================================================================
    assign DA0_Data = DA0_Data_internal;
    assign DA1_Data = DA1_Data_internal;
    assign DA0_Clk = DA_Clk_internal;
    assign DA1_Clk = DA_Clk_internal;

    //=========================================================================
    // 自定义序列发生器输出连接
    //=========================================================================
    assign SEQ_OUT = seq_out_internal;

    //=========================================================================
    // PWM输出连接
    //=========================================================================
    assign PWM_OUT = pwm_out_internal;

    //=========================================================================
    // I2C控制器 - 支持通用I2C主机写入和OLED两种模式
    // 通用I2C命令：0x70=主机写入（发送时序）
    // OLED专用命令：0x73=初始化, 0x74=清屏, 0x75=全亮, 0x76=显示文本
    //=========================================================================

    // I2C命令分类（基于当前命令码）
    wire i2c_generic_cmd = (cmd_code == 8'h70);                // 通用I2C写入
    wire oled_cmd = (cmd_code >= 8'h73 && cmd_code <= 8'h76); // OLED专用

    // 通用I2C控制器信号
    reg i2c_generic_cmd_req;
    reg [7:0] i2c_generic_cmd_code;
    // wire i2c_generic_cmd_done;  // 已在前面声明
    // wire [7:0] i2c_generic_response;  // 已在前面声明
    wire i2c_generic_scl;
    wire i2c_generic_sda;

    // OLED控制器信号
    reg oled_cmd_req;
    reg [7:0] oled_cmd_code_reg;
    // wire oled_cmd_done;  // 已在前面声明
    wire oled_i2c_scl;
    wire oled_i2c_sda;

    // I2C总线切换：根据实际的请求状态切换（而不是cmd_code）
    // 使用锁存的请求信号来判断哪个控制器在工作
    assign i2c_scl = i2c_generic_cmd_req ? i2c_generic_scl : oled_i2c_scl;
    assign i2c_sda = i2c_generic_cmd_req ? i2c_generic_sda : oled_i2c_sda;

    // 通用I2C命令锁存
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            i2c_generic_cmd_req <= 1'b0;
            i2c_generic_cmd_code <= 8'h0;
        end
        else if (cmd_done && i2c_generic_cmd) begin
            i2c_generic_cmd_req <= 1'b1;
            i2c_generic_cmd_code <= cmd_code;
        end
        else if (i2c_generic_cmd_done) begin
            i2c_generic_cmd_req <= 1'b0;
        end
    end

    // OLED命令锁存
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            oled_cmd_req <= 1'b0;
            oled_cmd_code_reg <= 8'h0;
        end
        else if (cmd_done && oled_cmd) begin
            oled_cmd_req <= 1'b1;
            oled_cmd_code_reg <= cmd_code;
        end
        else if (oled_cmd_done) begin
            oled_cmd_req <= 1'b0;
        end
    end

    // 通用I2C控制器实例化
    i2c_generic_controller u_i2c_generic_controller (
                               .clk               (clk),
                               .rst               (~sys_rst),
                               .cmd_valid         (i2c_generic_cmd_req),
                               .cmd_code          (i2c_generic_cmd_code),
                               .cmd_payload       (cmd_payload),
                               .cmd_payload_valid (cmd_payload_valid),
                               .payload_counter   (payload_counter),
                               .cmd_done          (i2c_generic_cmd_done),
                               .response_data     (i2c_generic_response),
                               .i2c_scl           (i2c_generic_scl),
                               .i2c_sda           (i2c_generic_sda)
                           );

    // OLED控制器实例化
    oled_controller u_oled_controller (
                        .clk         (clk),
                        .rst         (~sys_rst),
                        .cmd_valid   (oled_cmd_req),
                        .cmd_code    (oled_cmd_code_reg),
                        .cmd_done    (oled_cmd_done),
                        .i2c_scl     (oled_i2c_scl),
                        .i2c_sda     (oled_i2c_sda)
                    );

    //=========================================================================
    // SPI控制器 - 支持W25Q128 Flash和通用SPI传输
    // 命令：0x80=配置, 0x81=传输, 0x82=Flash读ID, 0x83=Flash读, 0x84=Flash写,
    //       0x85=扇区擦除, 0x86=全片擦除, 0x87=读状态
    //=========================================================================

    // SPI命令分类（基于当前命令码）
    wire spi_cmd = (cmd_code >= 8'h80 && cmd_code <= 8'h87);  // SPI命令 (0x80-0x87)

    // SPI控制器信号
    reg spi_cmd_req;
    reg [7:0] spi_cmd_code_reg;
    // wire spi_cmd_done;  // 已在前面声明
    wire [7:0] spi_response_data;
    wire spi_response_valid;  // 改为流式输出标志

    // SPI命令锁存
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            spi_cmd_req <= 1'b0;
            spi_cmd_code_reg <= 8'h0;
        end
        else if (cmd_done && spi_cmd) begin
            spi_cmd_req <= 1'b1;
            spi_cmd_code_reg <= cmd_code;
        end
        else if (spi_cmd_done) begin
            spi_cmd_req <= 1'b0;
        end
    end

    // SPI控制器实例化（优化版：流式输出）
    spi_controller u_spi_controller (
                       .clk               (clk),
                       .rst               (~sys_rst),
                       .cmd_valid         (spi_cmd_req),
                       .cmd_code          (spi_cmd_code_reg),
                       .cmd_payload       (cmd_payload),
                       .cmd_payload_valid (cmd_payload_valid),
                       .payload_counter   (payload_counter),
                       .cmd_done          (spi_cmd_done),
                       .response_data     (spi_response_data),
                       .response_valid    (spi_response_valid),  // 新接口
                       .spi_cs            (spi_cs),
                       .spi_sclk          (spi_sclk),
                       .spi_mosi          (spi_mosi),
                       .spi_miso          (spi_miso)
                   );

    //=========================================================================
    // DS18B20温度传感器控制器
    // 命令: 0xA0=读温度, 0xA1=开始监控, 0xA2=停止监控
    //=========================================================================
    wire ds18b20_cmd = (cmd_code >= 8'hA0 && cmd_code <= 8'hA2);

    // DS18B20控制器信号
    reg ds18b20_cmd_req;
    reg [7:0] ds18b20_cmd_code_reg;
    wire ds18b20_cmd_done;
    wire [15:0] ds18b20_temp_data;
    wire ds18b20_temp_valid;

    // DS18B20命令锁存
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            ds18b20_cmd_req <= 1'b0;
            ds18b20_cmd_code_reg <= 8'h0;
        end
        else if (cmd_done && ds18b20_cmd) begin
            ds18b20_cmd_req <= 1'b1;
            ds18b20_cmd_code_reg <= cmd_code;
        end
        else if (ds18b20_cmd_done) begin
            ds18b20_cmd_req <= 1'b0;
        end
    end

    // DS18B20控制器实例化
    ds18b20_controller u_ds18b20_controller (
                           .clk               (clk),
                           .rst_n             (~sys_rst),
                           .cmd_valid         (ds18b20_cmd_req),
                           .cmd_code          (ds18b20_cmd_code_reg),
                           .cmd_payload       (cmd_payload),
                           .cmd_payload_valid (cmd_payload_valid),
                           .payload_counter   (payload_counter),
                           .cmd_done          (ds18b20_cmd_done),
                           .temp_data         (ds18b20_temp_data),
                           .temp_valid        (ds18b20_temp_valid),
                           .dq                (ds18b20_dq)
                       );

    //=========================================================================
    // CAN总线控制器
    // 命令: 0xC0=配置波特率, 0xC1=发送帧, 0xC2=设置过滤器, 0xC3=读状态, 0xC4=接收数据
    //=========================================================================
    wire can_cmd = (cmd_code >= 8'hC0 && cmd_code <= 8'hC4);

    // CAN控制器信号
    wire can_response_valid;
    wire [7:0] can_resp_mod_id;
    wire [7:0] can_resp_func_id;
    wire [7:0] can_resp_status;
    wire [7:0] can_resp_data;
    wire can_rx_report_valid;
    wire [7:0] can_rx_report_data;
    wire can_rx_report_done;
    wire can_rx_report_ready_internal;  // 来自uart_tx_mux的ready信号

    // CAN原始接收数据（用于以太网UDP）
    wire can_rx_valid_raw;
    wire [7:0] can_rx_data_raw;
    wire can_rx_last_raw;
    wire [28:0] can_rx_id_raw;
    wire can_rx_ide_raw;

    // CAN控制器实例化
    can_controller u_can_controller (
                       .clk                  (clk),
                       .rst_n                (~sys_rst),
                       .cmd_code             (cmd_code),
                       .cmd_payload          (cmd_payload),
                       .cmd_payload_valid    (cmd_payload_valid),
                       .cmd_done             (cmd_done),
                       .payload_counter      (payload_counter),
                       .response_valid       (can_response_valid),
                       .resp_mod_id          (can_resp_mod_id),
                       .resp_func_id         (can_resp_func_id),
                       .resp_status          (can_resp_status),
                       .resp_data            (can_resp_data),
                       .response_done        (response_done_uart),
                       .can_rx_report_valid  (can_rx_report_valid),
                       .can_rx_report_data   (can_rx_report_data),
                       .can_rx_report_ready  (can_rx_report_ready_internal),  // ✅ 连接到UART MUX
                       .can_rx_report_done   (can_rx_report_done),
                       .can_rx_valid_raw     (can_rx_valid_raw),
                       .can_rx_data_raw      (can_rx_data_raw),
                       .can_rx_last_raw      (can_rx_last_raw),
                       .can_rx_id_raw        (can_rx_id_raw),
                       .can_rx_ide_raw       (can_rx_ide_raw),
                       .can_tx               (can_tx),
                       .can_rx               (can_rx)
                   );

    //=========================================================================
    // CAN以太网UDP发送模块
    // UDP端口: 6103 (独立于ADC示波器的6102)
    // 注意: 当前与ADC共享RGMII物理层，需要仲裁器或分时复用
    // TODO: 实现以太网TX仲裁器，或者将CAN数据复用到ADC的UDP通道
    //=========================================================================
    wire can_udp_tx_en;
    wire can_udp_tx_done;
    wire [15:0] can_udp_data_length;
    wire can_udp_payload_req;
    wire [7:0] can_udp_payload_data;

    can_udp_tx u_can_udp_tx (
                   .clk              (clk),
                   .rst_n            (~sys_rst),
                   .can_rx_valid     (can_rx_valid_raw),
                   .can_rx_data      (can_rx_data_raw),
                   .can_rx_last      (can_rx_last_raw),
                   .can_rx_id        (can_rx_id_raw),
                   .can_rx_ide       (can_rx_ide_raw),
                   .udp_tx_en        (can_udp_tx_en),
                   .udp_tx_done      (can_udp_tx_done),
                   .udp_data_length  (can_udp_data_length),
                   .udp_payload_req  (can_udp_payload_req),
                   .udp_payload_data (can_udp_payload_data)
               );

    // ⚠️ CAN UDP发送暂时未连接到RGMII物理层
    // 原因: 与ADC示波器共享RGMII，需要仲裁器避免总线冲突
    // 方案1: 实现以太网TX仲裁器（优先级: ADC > CAN）
    // 方案2: 将CAN数据合并到ADC的UDP流（使用不同端口号6103）
    // 方案3: 使用独立的以太网PHY（硬件层面隔离）
    assign can_udp_tx_done = 1'b0;  // 临时：未实现时返回失败

    //=========================================================================
    // SPI Flash读取数据流式发送（在应答帧后发送裸数据）
    //=========================================================================
    localparam SPI_TX_IDLE = 2'd0;
    localparam SPI_TX_WAIT_RESP = 2'd1;
    localparam SPI_TX_SEND_DATA = 2'd2;
    localparam SPI_TX_WAIT_UART = 2'd3;

    reg [1:0] spi_tx_state;

    // SPI数据FIFO缓冲区（扩大到256字节，解决UART发送慢导致的数据丢失）
    reg [7:0] spi_data_fifo [0:255];
    reg [7:0] spi_wr_ptr;
    reg [7:0] spi_rd_ptr;
    wire spi_fifo_empty;
    wire spi_fifo_full;

    assign spi_fifo_empty = (spi_wr_ptr == spi_rd_ptr);
    assign spi_fifo_full = ((spi_wr_ptr + 8'd1) == spi_rd_ptr);

    // 判断是否是需要返回数据的命令
    wire spi_need_data_return = (spi_cmd_code_reg == 8'h82) ||  // Flash读ID
         (spi_cmd_code_reg == 8'h83) ||  // Flash读取
         (spi_cmd_code_reg == 8'h87);    // Flash读状态

    // SPI FIFO写入逻辑
    reg spi_fifo_clear;  // 来自发送状态机的清空信号

    // SPI数据接收：当spi_response_valid有效时，写入FIFO
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            spi_wr_ptr <= 8'd0;
        end
        else if (spi_fifo_clear) begin
            // 发送完成后清空写指针
            spi_wr_ptr <= 8'd0;
        end
        else begin
            // 接收数据时写入FIFO
            if (spi_response_valid && !spi_fifo_full) begin
                spi_data_fifo[spi_wr_ptr] <= spi_response_data;
                spi_wr_ptr <= spi_wr_ptr + 8'd1;
            end
        end
    end

    // SPI数据发送状态机
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            spi_tx_state <= SPI_TX_IDLE;
            spi_data_tx_en <= 1'b0;
            spi_data_byte <= 8'h0;
            spi_rd_ptr <= 8'd0;
            spi_fifo_clear <= 1'b0;
        end
        else begin
            spi_fifo_clear <= 1'b0;  // 默认不清空

            case (spi_tx_state)
                SPI_TX_IDLE: begin
                    spi_data_tx_en <= 1'b0;

                    if (spi_cmd_done && spi_need_data_return && !spi_fifo_empty) begin
                        // SPI命令完成且需要返回数据，等待应答帧发完
                        spi_tx_state <= SPI_TX_WAIT_RESP;
                    end
                end

                SPI_TX_WAIT_RESP: begin
                    // 等待应答帧发送完成
                    if (response_done_uart) begin
                        spi_tx_state <= SPI_TX_SEND_DATA;
                    end
                end

                SPI_TX_SEND_DATA: begin
                    if (!spi_fifo_empty && !spi_data_tx_en) begin
                        // FIFO有数据，发送
                        spi_data_byte <= spi_data_fifo[spi_rd_ptr];
                        spi_data_tx_en <= 1'b1;
                        spi_rd_ptr <= spi_rd_ptr + 8'd1;
                        spi_tx_state <= SPI_TX_WAIT_UART;
                    end
                    else if (spi_fifo_empty) begin
                        // FIFO为空，数据发送完毕，通知清空写指针
                        spi_fifo_clear <= 1'b1;  // 发出清空信号
                        spi_rd_ptr <= 8'd0;      // 清空读指针
                        spi_tx_state <= SPI_TX_IDLE;
                    end
                end

                SPI_TX_WAIT_UART: begin
                    spi_data_tx_en <= 1'b0;
                    if (spi_data_uart_done) begin
                        // SPI数据通道UART发送完成，继续下一个字节
                        spi_tx_state <= SPI_TX_SEND_DATA;
                    end
                end

                default:
                    spi_tx_state <= SPI_TX_IDLE;
            endcase
        end
    end

    //=========================================================================
    // DSA数据发送（完全参照DS18B20模式 - 状态机自己发送完整帧）
    //=========================================================================
    localparam DSA_TX_IDLE = 3'd0;
    localparam DSA_TX_SEND_RESP = 3'd1;
    localparam DSA_TX_WAIT_RESP = 3'd2;
    localparam DSA_TX_SEND_DATA = 3'd3;
    localparam DSA_TX_WAIT_UART = 3'd4;

    reg [2:0] dsa_tx_state;
    reg [7:0] dsa_latched_data[12:0];  // 13字节：channel(1) + freq(4) + high(4) + low(4)
    reg [3:0] dsa_byte_index;
    reg [7:0] dsa_resp_frame[6:0];  // 🔥 新增：7字节应答帧缓存（参照DS18B20）

    // 🔥 应答帧预组装（参照DS18B20的ds18b20_resp_frame）
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            dsa_resp_frame[0] <= 8'hAA;
            dsa_resp_frame[1] <= 8'h55;
            dsa_resp_frame[2] <= 8'h01;  // MOD_ID
            dsa_resp_frame[3] <= 8'h68;  // FUNC_ID (读取命令0x68)
            dsa_resp_frame[4] <= 8'h00;  // STATUS (成功)
            dsa_resp_frame[5] <= 8'h00;  // Reserved
            dsa_resp_frame[6] <= 8'h69;  // Checksum = 01 ^ 68 ^ 00 = 69
        end
    end

    // 捕获result_valid并锁存13字节数据
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            dsa_latched_data[0] <= 8'd0;
            dsa_latched_data[1] <= 8'd0;
            dsa_latched_data[2] <= 8'd0;
            dsa_latched_data[3] <= 8'd0;
            dsa_latched_data[4] <= 8'd0;
            dsa_latched_data[5] <= 8'd0;
            dsa_latched_data[6] <= 8'd0;
            dsa_latched_data[7] <= 8'd0;
            dsa_latched_data[8] <= 8'd0;
            dsa_latched_data[9] <= 8'd0;
            dsa_latched_data[10] <= 8'd0;
            dsa_latched_data[11] <= 8'd0;
            dsa_latched_data[12] <= 8'd0;
        end
        else if (dsa_result_valid) begin
            // 锁存13字节数据（小端格式）
            dsa_latched_data[0] <= {5'd0, dsa_result_channel};  // channel
            dsa_latched_data[1] <= dsa_result_freq[7:0];        // freq低字节
            dsa_latched_data[2] <= dsa_result_freq[15:8];
            dsa_latched_data[3] <= dsa_result_freq[23:16];
            dsa_latched_data[4] <= dsa_result_freq[31:24];      // freq高字节
            dsa_latched_data[5] <= dsa_result_high_cycles[7:0]; // high_cycles低字节
            dsa_latched_data[6] <= dsa_result_high_cycles[15:8];
            dsa_latched_data[7] <= dsa_result_high_cycles[23:16];
            dsa_latched_data[8] <= dsa_result_high_cycles[31:24]; // high_cycles高字节
            dsa_latched_data[9] <= dsa_result_low_cycles[7:0];  // low_cycles低字节
            dsa_latched_data[10] <= dsa_result_low_cycles[15:8];
            dsa_latched_data[11] <= dsa_result_low_cycles[23:16];
            dsa_latched_data[12] <= dsa_result_low_cycles[31:24]; // low_cycles高字节
        end
    end

    // DSA发送状态机（完全参照DS18B20模式 - 自己发送7字节应答+13字节数据）
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            dsa_tx_state <= DSA_TX_IDLE;
            dsa_byte_index <= 4'd0;
            dsa_data_tx_en <= 1'b0;
            dsa_data_byte <= 8'd0;
        end
        else begin
            case (dsa_tx_state)
                DSA_TX_IDLE: begin
                    dsa_data_tx_en <= 1'b0;
                    dsa_byte_index <= 4'd0;

                    // 🔥 V8.8.0关键修复：增加全局使能检查，防止停止后残留数据阻塞UART
                    // 原理：0x67命令会立即关闭dsa_global_tx_enable，即使子模块仍输出
                    //       dsa_result_valid，状态机也会被此处的 && 条件拦截
                    // 参考：Gemini建议 - "强制截断DSA数据流，释放UART总线"
                    if (dsa_result_valid && dsa_global_tx_enable) begin
                        dsa_tx_state <= DSA_TX_SEND_RESP;
                    end
                end

                DSA_TX_SEND_RESP: begin
                    // 🔥 发送7字节应答帧（从dsa_resp_frame读取，参照DS18B20）
                    if (dsa_byte_index < 4'd7 && !dsa_data_tx_en) begin
                        dsa_data_byte <= dsa_resp_frame[dsa_byte_index];
                        dsa_data_tx_en <= 1'b1;
                        dsa_byte_index <= dsa_byte_index + 4'd1;
                        dsa_tx_state <= DSA_TX_WAIT_RESP;
                    end
                    else if (dsa_byte_index >= 4'd7) begin
                        // 应答帧发送完毕，开始发送13字节数据
                        dsa_byte_index <= 4'd0;
                        dsa_tx_state <= DSA_TX_SEND_DATA;
                    end
                end

                DSA_TX_WAIT_RESP: begin
                    dsa_data_tx_en <= 1'b0;
                    if (dsa_data_uart_done) begin  // 等待单字节UART发送完成
                        dsa_tx_state <= DSA_TX_SEND_RESP;  // 返回继续发送下一字节
                    end
                end

                DSA_TX_SEND_DATA: begin
                    // 发送13字节数据（逐字节发送）
                    if (dsa_byte_index < 4'd13 && !dsa_data_tx_en) begin
                        dsa_data_byte <= dsa_latched_data[dsa_byte_index];
                        dsa_data_tx_en <= 1'b1;
                        dsa_byte_index <= dsa_byte_index + 4'd1;
                        dsa_tx_state <= DSA_TX_WAIT_UART;
                    end
                    else if (dsa_byte_index >= 4'd13) begin
                        // 完整帧发送完毕（7字节应答+13字节数据）
                        dsa_tx_state <= DSA_TX_IDLE;
                    end
                end

                DSA_TX_WAIT_UART: begin
                    dsa_data_tx_en <= 1'b0;
                    if (dsa_data_uart_done) begin  // 等待单字节UART发送完成
                        dsa_tx_state <= DSA_TX_SEND_DATA;  // 返回继续发送下一字节
                    end
                end

                default:
                    dsa_tx_state <= DSA_TX_IDLE;
            endcase
        end
    end

    //=========================================================================
    // DS18B20温度数据流式发送（每次温度更新发送完整帧：应答帧7字节+温度2字节）
    //=========================================================================
    localparam DS18B20_TX_IDLE = 3'd0;
    localparam DS18B20_TX_SEND_RESP = 3'd1;  // 发送应答帧（7字节）
    localparam DS18B20_TX_WAIT_RESP_UART = 3'd2;
    localparam DS18B20_TX_SEND_DATA = 3'd3;  // 发送温度数据（2字节）
    localparam DS18B20_TX_WAIT_DATA_UART = 3'd4;

    reg [2:0] ds18b20_tx_state;
    reg [7:0] ds18b20_data_byte;
    reg ds18b20_data_tx_en;
    reg [2:0] ds18b20_byte_cnt;  // 总共9字节：7(应答)+2(温度)
    wire ds18b20_data_uart_done;

    // 应答帧固定内容（AA 55 MOD FUNC STATUS DATA[2] CHECKSUM）
    reg [7:0] ds18b20_resp_frame [0:6];
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            ds18b20_resp_frame[0] <= 8'hAA;      // 帧头1
            ds18b20_resp_frame[1] <= 8'h55;      // 帧头2
            ds18b20_resp_frame[2] <= 8'h01;      // MOD_ID (DS18B20模块)
            ds18b20_resp_frame[3] <= 8'hA0;      // FUNC_ID (读取命令)
            ds18b20_resp_frame[4] <= 8'h00;      // STATUS (成功)
            ds18b20_resp_frame[5] <= 8'h00;      // DATA[0]
            ds18b20_resp_frame[6] <= 8'h00;      // DATA[1]
        end
        else begin
            // 计算校验和：MOD ^ FUNC ^ STATUS ^ DATA[0] ^ DATA[1]
            ds18b20_resp_frame[6] <= ds18b20_resp_frame[2] ^ ds18b20_resp_frame[3] ^
                              ds18b20_resp_frame[4] ^ ds18b20_resp_frame[5] ^ 8'h00;
        end
    end

    // DS18B20数据发送状态机
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            ds18b20_tx_state <= DS18B20_TX_IDLE;
            ds18b20_data_tx_en <= 1'b0;
            ds18b20_data_byte <= 8'h0;
            ds18b20_byte_cnt <= 3'd0;
        end
        else begin
            case (ds18b20_tx_state)
                DS18B20_TX_IDLE: begin
                    ds18b20_data_tx_en <= 1'b0;
                    ds18b20_byte_cnt <= 3'd0;

                    if (ds18b20_temp_valid && ds18b20_reading_active) begin
                        // 温度数据有效且处于读取状态，开始发送完整帧
                        ds18b20_tx_state <= DS18B20_TX_SEND_RESP;
                    end
                end

                DS18B20_TX_SEND_RESP: begin
                    // 发送7字节应答帧
                    if (ds18b20_byte_cnt < 3'd7 && !ds18b20_data_tx_en) begin
                        ds18b20_data_byte <= ds18b20_resp_frame[ds18b20_byte_cnt];
                        ds18b20_data_tx_en <= 1'b1;
                        ds18b20_byte_cnt <= ds18b20_byte_cnt + 3'd1;
                        ds18b20_tx_state <= DS18B20_TX_WAIT_RESP_UART;
                    end
                    else if (ds18b20_byte_cnt >= 3'd7) begin
                        // 应答帧发送完毕，开始发送温度数据
                        ds18b20_byte_cnt <= 3'd0;  // 重置计数器用于温度数据
                        ds18b20_tx_state <= DS18B20_TX_SEND_DATA;
                    end
                end

                DS18B20_TX_WAIT_RESP_UART: begin
                    ds18b20_data_tx_en <= 1'b0;
                    if (ds18b20_data_uart_done) begin
                        ds18b20_tx_state <= DS18B20_TX_SEND_RESP;
                    end
                end

                DS18B20_TX_SEND_DATA: begin
                    // 发送2字节温度数据（低字节在前，小端序）
                    if (ds18b20_byte_cnt < 3'd2 && !ds18b20_data_tx_en) begin
                        if (ds18b20_byte_cnt == 3'd0)
                            ds18b20_data_byte <= ds18b20_temp_data[7:0];  // 低字节
                        else
                            ds18b20_data_byte <= ds18b20_temp_data[15:8]; // 高字节

                        ds18b20_data_tx_en <= 1'b1;
                        ds18b20_byte_cnt <= ds18b20_byte_cnt + 3'd1;
                        ds18b20_tx_state <= DS18B20_TX_WAIT_DATA_UART;
                    end
                    else if (ds18b20_byte_cnt >= 3'd2) begin
                        // 完整帧发送完毕（7字节应答+2字节温度）
                        ds18b20_tx_state <= DS18B20_TX_IDLE;
                    end
                end

                DS18B20_TX_WAIT_DATA_UART: begin
                    ds18b20_data_tx_en <= 1'b0;
                    if (ds18b20_data_uart_done) begin
                        ds18b20_tx_state <= DS18B20_TX_SEND_DATA;
                    end
                end

                default:
                    ds18b20_tx_state <= DS18B20_TX_IDLE;
            endcase
        end
    end

    //=========================================================================
    // 新增：ADC数据采集与USB回传模块（完整移植）
    //=========================================================================

    // ADC采集模块信号
    wire [7:0]  adc_fifo_wr_data;
    wire        adc_fifo_wr_en;
    wire        adc_stream_active;
    wire [31:0] adc_sample_counter;

    //-------------------------------------------------------------------------
    // ADC双通道采集模块（V3.1: 50MSPS基础采样率，Buffer模式限制25MSPS）
    // 当前配置：双通道采集，支持独立使能
    // ADC硬件：最大采样率 50MSPS
    // 系统时钟：50MHz，RESAMPLE_RATIO=1 → 基础采样率 50MSPS
    // Buffer模式限制：最大25MSPS（DDR3写入带宽瓶颈，实际约75MB/s < 50MSPS所需100MB/s）
    // 实际采样率 = 50MHz / div_set，通过上位机自适应算法控制
    // Buffer模式：div_set >= 2（最大25MSPS），支持信号范围 1Hz - 6.25MHz
    // 流模式：div_set >= 1（最大50MSPS），支持信号范围 1Hz - 12.5MHz
    //-------------------------------------------------------------------------
    //-------------------------------------------------------------------------
    // 双通道ADC采集（通道1）
    //-------------------------------------------------------------------------
    wire [7:0] adc_ch1_data;
    wire adc_ch1_valid;
    wire adc_ch1_stream_active;
    wire [31:0] adc_ch1_sample_counter;
    wire adc_ch1_capture_done;
    wire [3:0] adc_ch1_state;
    wire adc_ch1_busy;

    //-------------------------------------------------------------------------
    // 双通道ADC采集（通道2）信号定义
    //-------------------------------------------------------------------------
    wire adc_ch2_stream_active;

    // 🔥 V8.7.58: ADC背压信号根据模式选择
    //   - Buffer模式：使用DDR3背压（ddr3_wr_fifo_full || ddr3_almost_full）
    //   - 流模式：使用以太网FIFO背压（eth_fifo_full）
    //   - 修复50MSPS时Buffer模式数据溢出问题
    wire adc_ch1_backpressure;
    assign adc_ch1_backpressure = (adc_mode == 1'b1) ?
           (ddr3_wr_fifo_full || ddr3_almost_full) :
           eth_fifo_full;

    adc_capture_stream #(
                           .RESAMPLE_RATIO(1)  // 50MSPS基础采样率
                       ) u_adc_capture_ch1 (
                           .sys_clk        (clk),
                           .sys_rst_n      (~sys_rst),
                           .adc_clk_180    (adc_clk_50m),
                           .adc_data_a     (adc_data_a),           // 通道1数据输入
                           .adc_data_b     (8'd0),                 // 未使用
                           .adc_clk_out_a  (adc_clk_out_a),
                           .adc_clk_out_b  (),
                           .fifo_wr_data   (adc_ch1_data),
                           .fifo_wr_en     (adc_ch1_valid),
                           .fifo_full      (adc_ch1_backpressure),
                           .stream_active  (adc_ch1_stream_active),
                           .sample_counter (adc_ch1_sample_counter),
                           .mode_select    (adc_mode),
                           .capture_start  (adc_start_cmd),
                           .capture_stop   (adc_stop_cmd),
                           .capture_length (adc_buffer_size),
                           .capture_done   (adc_ch1_capture_done),
                           .trigger_en     (1'b0),  // 🔥 禁用ADC内部触发，使用顶层统一触发
                           .trigger_level  (16'd0),
                           .trigger_edge   (1'b0),
                           .trigger_detected (),
                           .adc_state      (adc_ch1_state),
                           .adc_busy       (adc_ch1_busy),
                           .div_set        (adc_sample_div)
                       );

    //-------------------------------------------------------------------------
    // 双通道ADC采集（通道2）
    //-------------------------------------------------------------------------
    wire [7:0] adc_ch2_data;
    wire adc_ch2_valid;

    wire adc_ch2_backpressure;
    assign adc_ch2_backpressure = (adc_mode == 1'b1) ?
           (ddr3_wr_fifo_full || ddr3_almost_full) :
           eth_fifo_full;

    adc_capture_stream #(
                           .RESAMPLE_RATIO(1)
                       ) u_adc_capture_ch2 (
                           .sys_clk        (clk),
                           .sys_rst_n      (~sys_rst),
                           .adc_clk_180    (adc_clk_50m),
                           .adc_data_a     (adc_data_b),           // 通道2数据输入
                           .adc_data_b     (8'd0),
                           .adc_clk_out_a  (adc_clk_out_b),
                           .adc_clk_out_b  (),
                           .fifo_wr_data   (adc_ch2_data),
                           .fifo_wr_en     (adc_ch2_valid),
                           .fifo_full      (adc_ch2_backpressure),
                           .stream_active  (adc_ch2_stream_active),  // 🔥 修复：连接CH2采集状态
                           .sample_counter (),
                           .mode_select    (adc_mode),
                           .capture_start  (adc_start_cmd),
                           .capture_stop   (adc_stop_cmd),
                           .capture_length (adc_buffer_size),
                           .capture_done   (),
                           .trigger_en     (1'b0),  // 🔥 禁用ADC内部触发，使用顶层统一触发
                           .trigger_level  (16'd0),
                           .trigger_edge   (1'b0),
                           .trigger_detected (),
                           .adc_state      (),
                           .adc_busy       (),
                           .div_set        (adc_sample_div)
                       );

    // 使用通道1的状态信号作为全局状态
    // 🔥 V8.6.18修复：支持单独CH2采集，任一通道激活即可触发频率测量
    assign adc_stream_active = adc_ch1_stream_active | adc_ch2_stream_active;
    assign adc_sample_counter = adc_ch1_sample_counter;
    assign adc_capture_done = adc_ch1_capture_done;
    assign adc_state = adc_ch1_state;
    assign adc_busy = adc_ch1_busy;
    assign adc_captured_count = adc_ch1_sample_counter;

    //-------------------------------------------------------------------------
    // 双通道ADC数据交织器（8bit+8bit → 16bit）
    //-------------------------------------------------------------------------
    wire [15:0] adc_interleaved_data;
    wire adc_interleaved_valid;
    wire        eth_fifo_full;  // 🔥 V8.6.30: 提前声明以供interleaver使用

    adc_dual_channel_interleaver u_adc_interleaver (
                                     .clk            (clk),
                                     .rst_n          (~sys_rst),
                                     // 🔥 V5.0: 通道使能控制
                                     .ch1_enable     (ch1_enable),
                                     .ch2_enable     (ch2_enable),
                                     // 双通道数据输入
                                     .ch1_data       (adc_ch1_data),
                                     .ch1_valid      (adc_ch1_valid),
                                     .ch2_data       (adc_ch2_data),
                                     .ch2_valid      (adc_ch2_valid),
                                     // 🔥 V8.7.58: 修复背压信号 - 使用DDR3 FIFO满+接近满信号
                                     //             解决50MSPS时DDR3写入慢导致的数据溢出问题
                                     .fifo_full      (ddr3_wr_fifo_full || ddr3_almost_full),
                                     // 交织输出
                                     .interleaved_data (adc_interleaved_data),
                                     .interleaved_valid (adc_interleaved_valid)
                                 );
    // 🔥 V8.7.59: 移除重复的背压检查，避免死锁
    // 原因：交织器已经根据背压停止输出（fifo_full = ddr3_wr_fifo_full || ddr3_almost_full）
    // 这里再检查!ddr3_wr_fifo_full会导致adc_sample_cnt无法增加，造成50MSPS死锁
    // 修复：信任交织器的背压控制，直接使用interleaved_valid
    assign adc_data_16bit = adc_interleaved_data;
    assign adc_data_16bit_valid = adc_interleaved_valid && (adc_mode == 1'b1);

    //-------------------------------------------------------------------------
    // Buffer模式触发检测器 (V8.7.0)
    //-------------------------------------------------------------------------
    // V8.7.1: 统一触发检测模块（流模式+Buffer模式共用）
    //-------------------------------------------------------------------------
    wire trigger_detected;      // 触发检测到信号（单周期脉冲）
    wire [31:0] trigger_pos;    // 触发位置（采样点数）
    reg trigger_enable_internal; // 内部触发使能（由状态机控制）

    // 选择触发通道的数据（8位→16位扩展）
    wire [7:0]  trigger_adc_data_8bit;
    wire [15:0] trigger_adc_data;
    assign trigger_adc_data_8bit = trigger_channel ? adc_ch2_data : adc_ch1_data;
    assign trigger_adc_data = {trigger_adc_data_8bit, 8'h00};  // 扩展到16位

    // 选择触发通道的有效信号
    wire trigger_adc_valid;
    assign trigger_adc_valid = trigger_channel ? adc_ch2_valid : adc_ch1_valid;

    // 触发电平位宽扩展（8位→16位，与ADC数据对齐）
    wire [15:0] trigger_level_16bit;
    assign trigger_level_16bit = {trigger_level, 8'h00};

    trigger_detector u_trigger (
                         .clk            (clk),
                         .rst_n          (~sys_rst),
                         .trigger_enable (trigger_enable_internal),  // 由状态机控制
                         .adc_data       (trigger_adc_data),         // 16位扩展后的ADC数据
                         .adc_valid      (trigger_adc_valid),
                         .trigger_level  (trigger_level_16bit),      // 16位扩展后的触发电平
                         .trigger_edge   (trigger_edge),
                         .trigger_mode   (2'b01),                    // 固定正常模式(等待触发)
                         .triggered      (trigger_detected),         // 触发脉冲
                         .trigger_pos    (trigger_pos)               // 触发位置
                     );

    // 保留旧信号以兼容频率测量模块
    assign adc_fifo_wr_data = adc_ch1_data;
    assign adc_fifo_wr_en = adc_ch1_valid;

    //-------------------------------------------------------------------------
    // ADC数据锁存逻辑（解决跨时钟域和占空比采样问题）
    //-------------------------------------------------------------------------
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            adc_data_latched <= 16'd0;
            adc_data_valid_latched <= 1'b0;
        end
        else begin
            if (adc_data_16bit_valid) begin
                adc_data_latched <= adc_data_16bit;
                adc_data_valid_latched <= 1'b1;
            end
            else begin
                adc_data_valid_latched <= 1'b0;
            end
        end
    end

    //=========================================================================
    // 新增：逻辑分析仪数据采集模块
    //=========================================================================

    //-------------------------------------------------------------------------
    // 逻辑分析仪采集模块（8通道数字信号采样）
    // V2.0简化版：直接使用50MHz系统时钟采样，移除异步FIFO
    // 采样时钟：50MHz（系统时钟）
    // 采样率：可配置分频（1=50MSPS, 2=25MSPS, 5=10MSPS, 10=5MSPS...）
    // 数据流：LOGIC_IN[7:0] → 采样寄存器 → 直接输出 → USB CDC
    //-------------------------------------------------------------------------
    logic_analyzer_capture u_logic_analyzer_capture (
                               .clk            (clk),                  // 50MHz系统时钟（采样和输出）
                               .rst_n          (~sys_rst),

                               // 输入信号
                               .logic_in       (LOGIC_IN),             // 8通道数字输入

                               // 控制接口 - 🔥 使用延迟后的启动信号
                               .capture_en     (la_capture_en_delayed),// 延迟的采集使能（FIFO清空后才启动）
                               .capture_stop   (la_capture_stop),      // 停止采集
                               .sample_div     (la_sample_div),        // 采样分频系数（基于50MHz）
                               .capture_len    (la_capture_len),       // 采集长度

                               // 触发配置
                               .trigger_en     (la_trigger_en),        // 触发使能
                               .trigger_mask   (la_trigger_mask),      // 触发掩码
                               .trigger_value  (la_trigger_value),     // 触发值

                               // FIFO接口（直接连接到多路选择器）
                               .fifo_data      (la_fifo_rd_data),      // 读数据（连到多路选择器）
                               .fifo_wr_en     (la_fifo_wr_en),        // 写使能（连到多路选择器）
                               .fifo_full      (la_tx_almost_full),

                               // 状态输出
                               .captured_count (la_captured_count),    // 已采集字节数
                               .capture_done   (la_capture_done),      // 采集完成标志
                               .state          (la_state),             // 状态机状态
                               .trigger_detected(la_trigger_detected)  // 触发检测标志
                           );

    // 注意：逻辑分析仪复用ADC的FIFO和USB通道，通过多路选择器切换
    // 当eth_transfer_active=0且la_usb_enable=1时，选择LA数据
    // V2.0简化：同时钟域直接输出，无内部FIFO
    assign la_fifo_empty = !la_fifo_wr_en;  // 当LA模块输出数据时认为非空

    //=========================================================================
    // 新增：频率测量使能控制逻辑和频率值锁存（双通道支持）
    //=========================================================================
    // CH1频率显示
    reg [31:0] measured_freq_latched_ch1;
    reg freq_display_valid_ch1;
    reg [31:0] measured_freq_bcd_ch1;
    reg bcd_convert_req_ch1;
    wire [31:0] freq_bcd_out_ch1;
    wire freq_bcd_done_ch1;

    // CH2频率显示
    reg [31:0] measured_freq_latched_ch2;
    reg freq_display_valid_ch2;
    reg [31:0] measured_freq_bcd_ch2;
    reg bcd_convert_req_ch2;
    wire [31:0] freq_bcd_out_ch2;
    wire freq_bcd_done_ch2;

    // CH1频率锁存逻辑
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            measured_freq_latched_ch1 <= 32'h0;
            measured_freq_bcd_ch1 <= 32'h0;
            freq_display_valid_ch1 <= 1'b0;
            bcd_convert_req_ch1 <= 1'b0;
        end
        else if (adc_stop_cmd) begin
            measured_freq_bcd_ch1 <= 32'h0;
            freq_display_valid_ch1 <= 1'b0;
            bcd_convert_req_ch1 <= 1'b0;
        end
        else if (freq_valid_ch1 && adc_stream_active) begin
            measured_freq_latched_ch1 <= measured_frequency_ch1;
            bcd_convert_req_ch1 <= 1'b1;
        end
        else if (!adc_stream_active && adc_stream_active_d1) begin
            freq_display_valid_ch1 <= 1'b0;
            measured_freq_bcd_ch1 <= 32'h0;
        end
        else if (freq_bcd_done_ch1) begin
            measured_freq_bcd_ch1 <= freq_bcd_out_ch1;
            freq_display_valid_ch1 <= 1'b1;
            bcd_convert_req_ch1 <= 1'b0;
        end
        else begin
            bcd_convert_req_ch1 <= 1'b0;
        end
    end

    // CH2频率锁存逻辑
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            measured_freq_latched_ch2 <= 32'h0;
            measured_freq_bcd_ch2 <= 32'h0;
            freq_display_valid_ch2 <= 1'b0;
            bcd_convert_req_ch2 <= 1'b0;
        end
        else if (adc_stop_cmd) begin
            measured_freq_bcd_ch2 <= 32'h0;
            freq_display_valid_ch2 <= 1'b0;
            bcd_convert_req_ch2 <= 1'b0;
        end
        else if (freq_valid_ch2 && adc_stream_active) begin
            measured_freq_latched_ch2 <= measured_frequency_ch2;
            bcd_convert_req_ch2 <= 1'b1;
        end
        else if (!adc_stream_active && adc_stream_active_d1) begin
            freq_display_valid_ch2 <= 1'b0;
            measured_freq_bcd_ch2 <= 32'h0;
        end
        else if (freq_bcd_done_ch2) begin
            measured_freq_bcd_ch2 <= freq_bcd_out_ch2;
            freq_display_valid_ch2 <= 1'b1;
            bcd_convert_req_ch2 <= 1'b0;
        end
        else begin
            bcd_convert_req_ch2 <= 1'b0;
        end
    end

    // 向后兼容：保留旧信号名（默认使用CH1）- 改为wire类型
    wire [31:0] measured_freq_latched;
    wire freq_display_valid;
    wire [31:0] measured_freq_bcd;
    wire bcd_convert_req;
    wire [31:0] freq_bcd_out;
    wire freq_bcd_done;

    assign measured_freq_latched = measured_freq_latched_ch1;
    assign freq_display_valid = freq_display_valid_ch1;
    assign measured_freq_bcd = measured_freq_bcd_ch1;
    assign bcd_convert_req = bcd_convert_req_ch1;
    assign freq_bcd_out = freq_bcd_out_ch1;
    assign freq_bcd_done = freq_bcd_done_ch1;    //=========================================================================
    // 新增：双通道BCD转换模块
    //=========================================================================
    bin_to_bcd u_freq_bcd_converter_ch1 (
                   .clk         (clk),
                   .rst_n       (~sys_rst),
                   .binary      (measured_freq_latched_ch1),
                   .convert_en  (bcd_convert_req_ch1),
                   .bcd         (freq_bcd_out_ch1),
                   .done        (freq_bcd_done_ch1)
               );

    bin_to_bcd u_freq_bcd_converter_ch2 (
                   .clk         (clk),
                   .rst_n       (~sys_rst),
                   .binary      (measured_freq_latched_ch2),
                   .convert_en  (bcd_convert_req_ch2),
                   .bcd         (freq_bcd_out_ch2),
                   .done        (freq_bcd_done_ch2)
               );

    //=========================================================================
    // 新增：频率测量模块 (双通道支持)
    //=========================================================================

    //-------------------------------------------------------------------------
    // 通道1 (CH1) 频率测量
    //-------------------------------------------------------------------------
    // 过零比较器: ADC数字信号 → 方波
    wire zero_cross_signal_ch1;
    wire zero_cross_valid_ch1;

    zero_cross_comparator u_zero_cross_cmp_ch1 (
                              .clk              (clk),
                              .rst_n            (~sys_rst),
                              .adc_data         (adc_data_a),           // ADC通道1数据
                              .adc_data_valid   (1'b1),                 // ADC数据始终有效
                              .threshold_high   (8'd140),               // 高阈值
                              .threshold_low    (8'd114),               // 低阈值
                              .signal_out       (zero_cross_signal_ch1),
                              .signal_valid     (zero_cross_valid_ch1)
                          );

    // 频率计数器: 方波 → 频率值
    wire [31:0] measured_frequency_ch1;
    wire freq_valid_ch1;
    wire freq_measuring_ch1;

    frequency_counter u_frequency_counter_ch1 (
                          .clk              (clk),
                          .rst_n            (~sys_rst),

                          // 输入信号
                          .signal_in        (zero_cross_signal_ch1),
                          .signal_valid     (zero_cross_valid_ch1),

                          // 控制接口
                          .measure_start    (freq_measure_start),   // 测量启动脉冲
                          .gate_time        (32'd50_000_000),       // 1秒门控时间

                          // 输出接口
                          .freq_out         (measured_frequency_ch1),
                          .freq_valid       (freq_valid_ch1),
                          .measuring        (freq_measuring_ch1)
                      );

    //-------------------------------------------------------------------------
    // 通道2 (CH2) 频率测量
    //-------------------------------------------------------------------------
    // 过零比较器: ADC数字信号 → 方波
    wire zero_cross_signal_ch2;
    wire zero_cross_valid_ch2;

    zero_cross_comparator u_zero_cross_cmp_ch2 (
                              .clk              (clk),
                              .rst_n            (~sys_rst),
                              .adc_data         (adc_data_b),           // ADC通道2数据
                              .adc_data_valid   (1'b1),                 // ADC数据始终有效
                              .threshold_high   (8'd140),               // 高阈值
                              .threshold_low    (8'd114),               // 低阈值
                              .signal_out       (zero_cross_signal_ch2),
                              .signal_valid     (zero_cross_valid_ch2)
                          );

    // 频率计数器: 方波 → 频率值
    wire [31:0] measured_frequency_ch2;
    wire freq_valid_ch2;
    wire freq_measuring_ch2;

    frequency_counter u_frequency_counter_ch2 (
                          .clk              (clk),
                          .rst_n            (~sys_rst),

                          // 输入信号
                          .signal_in        (zero_cross_signal_ch2),
                          .signal_valid     (zero_cross_valid_ch2),

                          // 控制接口
                          .measure_start    (freq_measure_start),   // 测量启动脉冲
                          .gate_time        (32'd50_000_000),       // 1秒门控时间

                          // 输出接口
                          .freq_out         (measured_frequency_ch2),
                          .freq_valid       (freq_valid_ch2),
                          .measuring        (freq_measuring_ch2)
                      );

    // 向后兼容：保留旧信号名（默认使用CH1）
    wire [31:0] measured_frequency;
    wire freq_valid;
    wire freq_measuring;
    assign measured_frequency = measured_frequency_ch1;
    assign freq_valid = freq_valid_ch1;
    assign freq_measuring = freq_measuring_ch1;

    //=========================================================================
    // 🔥 新增：自动测频定时器（每1秒触发一次）
    //=========================================================================
    reg auto_freq_timer_reset;  // 定时器重置请求信号

    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            auto_freq_timer <= 32'd0;
            auto_freq_trigger <= 1'b0;
        end
        else begin
            // 🔥 如果收到重置请求（手动测频时），重置定时器
            if (auto_freq_timer_reset) begin
                auto_freq_timer <= 32'd0;
                auto_freq_trigger <= 1'b0;
            end
            // 每1秒触发一次
            else if (auto_freq_timer >= AUTO_FREQ_INTERVAL - 1) begin
                auto_freq_timer <= 32'd0;
                auto_freq_trigger <= 1'b1;  // 产生单周期脉冲
            end
            else begin
                auto_freq_timer <= auto_freq_timer + 32'd1;
                auto_freq_trigger <= 1'b0;
            end
        end
    end

    //=========================================================================
    // 频率测量触发逻辑（V2.0：独立测频架构，移除DSA互斥检查）
    // 架构改进：
    //   - ADC使用独立的frequency_counter实例（u_frequency_counter_ch1/ch2）
    //   - DSA使用各自内部的frequency_counter实例（8个独立实例）
    //   - 无资源竞争，可以并发测量
    // 日期：2025-11-27
    //=========================================================================
    reg freq_cmd_pending;  // 频率命令等待应答帧完成
    reg freq_measure_start;  // 频率测量启动信号（传递给frequency_tx_controller）

    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            freq_cmd_pending <= 1'b0;
            freq_measure_start <= 1'b0;
            auto_freq_timer_reset <= 1'b0;
            freq_cmd_response_pending <= 1'b0;  // 🔥 新增复位
        end
        else begin
            // 默认不重置定时器
            auto_freq_timer_reset <= 1'b0;
            freq_measure_start <= 1'b0;  // 🔥 默认清零（避免意外触发）

            // 🔥 手动测频（0x27命令）：最高优先级
            if (freq_measure_request) begin
                freq_measure_start <= 1'b1;      // 🔥 直接启动测量
                auto_freq_timer_reset <= 1'b1;  // 🔥 重置定时器，避免冲突
            end
            // 🔥 V2.0重大改进：移除 !dsa_measuring 条件
            // 原因：ADC和DSA现在使用完全独立的frequency_counter实例
            //      无需互斥保护，可以并发测量
            // 条件：定时器触发 && 测量空闲 && 无手动命令 && ADC采集激活中
            else if (auto_freq_trigger && !freq_measuring && !freq_cmd_pending && adc_stream_active) begin
                freq_measure_start <= 1'b1;
            end
        end
    end

    //=========================================================================
    // 新增：频率数据发送控制器
    //=========================================================================
    wire freq_tx_wait_valid;  // 等待freq_valid的LED信号
    wire freq_tx_led_sending; // 正在发送的LED信号

    frequency_tx_controller u_freq_tx_ctrl (
                                .clk            (clk),
                                .rst_n          (~sys_rst),

                                // 双通道频率数据输入（V2.0）
                                .frequency_ch1  (measured_frequency_ch1),
                                .frequency_ch2  (measured_frequency_ch2),
                                .freq_valid_ch1 (freq_valid_ch1),
                                .freq_valid_ch2 (freq_valid_ch2),
                                .freq_request   (freq_measure_start),  // 应答帧完成后触发

                                // UART发送接口（连接到MUX）
                                .tx_data        (freq_tx_data),
                                .tx_send_en     (freq_tx_send_en),
                                .tx_done        (freq_tx_done),
                                .tx_busy        (uart_tx_busy),

                                // 状态输出
                                .sending        (freq_sending),
                                .tx_state_debug (),

                                // LED调试输出
                                .led_wait_valid (freq_tx_wait_valid),
                                .led_sending    (freq_tx_led_sending)
                            );

    // UART忙标志（✅ 修复：应该使用uart_state，而不是uart_tx_send_en_mux）
    // uart_state=1表示UART硬件正在发送，此时不应该启动新的发送
    // uart_tx_send_en_mux只是单周期脉冲，不能表示发送状态
    assign uart_tx_busy = uart_state;

    //-------------------------------------------------------------------------
    // DDR3双端口控制器
    //-------------------------------------------------------------------------
    ddr3_ctrl_2port u_ddr3_ctrl (
                        .clk              (clk),
                        .pll_lock         (pll_lock_ddr),
                        .pll_stop         (pll_stop),
                        .clk_400m         (loc_clk400m),
                        .sys_rst_n        (reset_n),
                        .init_calib_complete (ddr3_init_done),

                        // 读通道配置
                        .rd_load          (ddr3_rd_load),
                        .app_addr_rd_min  (28'd0),
                        .app_addr_rd_max  ({ddr3_write_count[26:0], 1'b0}),  // 🔥 V8.7.9: Buffer模式读取实际写入的数据量
                        .rd_bust_len      (burst_len),

                        // 写通道配置
                        .wr_load          (ddr3_wr_load),
                        .app_addr_wr_min  (28'd0),
                        .app_addr_wr_max  (app_addr_max),     // 256MB
                        .wr_bust_len      (burst_len),

                        // 写FIFO接口（ADC → DDR3）
                        .wr_clk           (clk),
                        .wfifo_wren       (adc_data_valid_latched && ddr3_data_valid && !ddr3_wr_fifo_full && !ddr3_almost_full),
                        .wfifo_din        (adc_data_latched),
                        .wrfifo_full      (ddr3_wr_fifo_full),

                        // 读FIFO接口（DDR3 → USB）
                        .rd_clk           (clk),
                        .rfifo_rden       (ddr3_rd_fifo_rden),
                        .rdfifo_empty     (ddr3_rd_fifo_empty),
                        .rfifo_dout       (ddr3_rd_data),

                        // DDR3物理接口
                        .ddr3_dq          (IO_ddr_dq),
                        .ddr3_dqs_n       (IO_ddr_dqs_n),
                        .ddr3_dqs_p       (IO_ddr_dqs),
                        .ddr3_addr        (O_ddr_addr),
                        .ddr3_ba          (O_ddr_ba),
                        .ddr3_ras_n       (O_ddr_ras_n),
                        .ddr3_cas_n       (O_ddr_cas_n),
                        .ddr3_we_n        (O_ddr_we_n),
                        .ddr3_reset_n     (O_ddr_reset_n),
                        .ddr3_ck_p        (O_ddr_clk),
                        .ddr3_ck_n        (O_ddr_clk_n),
                        .ddr3_cke         (O_ddr_cke),
                        .ddr3_cs_n        (O_ddr_cs_n),
                        .ddr3_dm          (O_ddr_dqm),
                        .ddr3_odt         (O_ddr_odt)
                    );

    //-------------------------------------------------------------------------
    // DDR3读取三级流水线（消除高频时序问题）
    //-------------------------------------------------------------------------
    // 第一级：锁存读使能和空标志
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            ddr3_rd_fifo_rden_stage1 <= 1'b0;
            ddr3_rd_fifo_empty_stage1 <= 1'b1;
        end
        // 🔥 V8.7.60: Buffer模式状态转换时清零流水线，防止残留数据
        else if (adc_mode == 1'b1 && ddr3_ctrl_state == DDR3_IDLE && ddr3_ctrl_state_d1 != DDR3_IDLE) begin
            ddr3_rd_fifo_rden_stage1 <= 1'b0;
            ddr3_rd_fifo_empty_stage1 <= 1'b1;
        end
        else begin
            // 🔥 V8.7.8关键修复: Buffer模式移除ddr3_safe_to_continue检查
            // 原因：Buffer模式采集量小（10K点=20KB）< 48KB阈值，永远无法读取！
            // 参考USB版本：只检查empty和transfer_active
            ddr3_rd_fifo_rden_stage1 <= (eth_transfer_active
                                         && !ddr3_rd_fifo_empty
                                         && !eth_fifo_full);
            ddr3_rd_fifo_empty_stage1 <= ddr3_rd_fifo_empty;
        end
    end

    // 第二级：锁存DDR3读出的数据
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            ddr3_rd_data_stage2 <= 16'd0;
            ddr3_rd_data_valid_stage2 <= 1'b0;
        end
        // 🔥 V8.7.60: Buffer模式状态转换时清零流水线第二级
        else if (adc_mode == 1'b1 && ddr3_ctrl_state == DDR3_IDLE && ddr3_ctrl_state_d1 != DDR3_IDLE) begin
            ddr3_rd_data_stage2 <= 16'd0;
            ddr3_rd_data_valid_stage2 <= 1'b0;
        end
        else begin
            if (ddr3_rd_fifo_rden_stage1 && !ddr3_rd_fifo_empty_stage1) begin
                ddr3_rd_data_stage2 <= ddr3_rd_data;
                ddr3_rd_data_valid_stage2 <= 1'b1;
            end
            else begin
                ddr3_rd_data_stage2 <= 16'd0;
                ddr3_rd_data_valid_stage2 <= 1'b0;
            end
        end
    end

    // 第三级：最终输出
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            ddr3_rd_data_stage3 <= 16'd0;
            ddr3_rd_data_valid_stage3 <= 1'b0;
        end
        // 🔥 V8.7.60: Buffer模式状态转换时清零流水线第三级
        else if (adc_mode == 1'b1 && ddr3_ctrl_state == DDR3_IDLE && ddr3_ctrl_state_d1 != DDR3_IDLE) begin
            ddr3_rd_data_stage3 <= 16'd0;
            ddr3_rd_data_valid_stage3 <= 1'b0;
        end
        else begin
            ddr3_rd_data_stage3 <= ddr3_rd_data_stage2;
            ddr3_rd_data_valid_stage3 <= ddr3_rd_data_valid_stage2;
        end
    end

    // DDR3读写控制逻辑（V2.0版本 - 支持模式切换）
    // V8.7.0: 添加Buffer模式触发支持
    // 🔥 V8.7.51: 添加WAIT_FIFO_CLEAR状态，解决FIFO残留数据问题
    //-------------------------------------------------------------------------
    localparam DDR3_IDLE = 3'd0;
    localparam DDR3_WAIT_TRIGGER = 3'd1;  // 新增：等待触发状态
    localparam DDR3_CAPTURING = 3'd2;
    localparam DDR3_WAIT_FIFO_CLEAR = 3'd3;  // 🔥 V8.7.51新增：等待FIFO清空状态
    localparam DDR3_TRANSFERRING = 3'd4;
    localparam DDR3_DONE = 3'd5;

    reg [2:0] ddr3_ctrl_state;  // 3位支持6个状态

    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            ddr3_wr_load_reg <= 1'b0;
            ddr3_rd_load_reg <= 1'b0;
            ddr3_init_done_d1 <= 1'b0;
            adc_stream_active_d1 <= 1'b0;
            adc_sample_cnt <= 32'd0;
            rd_started <= 1'b0;
            wr_load_pulse_cnt <= 5'd0;
            rd_load_pulse_cnt <= 5'd0;
            data_ready_flag <= 1'b0;
            eth_transfer_active <= 1'b0;
            ddr3_ctrl_state <= DDR3_IDLE;
            ddr3_data_valid <= 1'b0;
            ddr3_write_count <= 32'd0;
            ddr3_read_count <= 32'd0;
            ddr3_data_count <= 28'd0;
            done_delay_cnt <= 16'd0;
            trigger_enable_internal <= 1'b0;
        end
        else begin
            ddr3_init_done_d1 <= ddr3_init_done;
            adc_stream_active_d1 <= adc_stream_active;

            // 模式选择：流模式 vs Buffer模式
            case (adc_mode)
                //=====================================================================
                // 流模式：直接传输，不使用DDR3（V5.0优化：零延迟）
                //=====================================================================
                1'b0: begin
                    // 流模式保持DDR3空闲，直接传输ADC数据到以太网FIFO
                    ddr3_ctrl_state <= DDR3_IDLE;
                    ddr3_wr_load_reg <= 1'b0;
                    ddr3_rd_load_reg <= 1'b0;
                    data_ready_flag <= 1'b0;
                    rd_load_pulse_cnt <= 5'd0;
                    adc_sample_cnt <= 32'd0;
                    rd_started <= 1'b0;
                    ddr3_data_valid <= 1'b0;
                    ddr3_write_count <= 32'd0;
                    ddr3_read_count <= 32'd0;
                    ddr3_data_count <= 28'd0;
                    done_delay_cnt <= 16'd0;
                    trigger_enable_internal <= 1'b0;  // 流模式不使用统一触发

                    // eth_transfer_active由adc_stream_active直接控制（在以太网控制器中）
                    eth_transfer_active <= 1'b0;

                end

                1'b1: begin // Buffer模式 (V8.7.0: 添加触发支持)
                    case (ddr3_ctrl_state)
                        DDR3_IDLE: begin
                            data_ready_flag <= 1'b0;
                            eth_transfer_active <= 1'b0;
                            rd_started <= 1'b0;
                            adc_sample_cnt <= 32'd0;
                            ddr3_data_valid <= 1'b0;
                            ddr3_write_count <= 32'd0;
                            ddr3_read_count <= 32'd0;
                            ddr3_data_count <= 28'd0;
                            done_delay_cnt <= 16'd0;
                            ddr3_wr_load_reg <= 1'b0;
                            ddr3_rd_load_reg <= 1'b0;
                            trigger_enable_internal <= 1'b0;
                            wr_load_pulse_cnt <= 5'd0;  // 🔥 V8.7.27: 清除写load计数器
                            rd_load_pulse_cnt <= 5'd0;  // 🔥 V8.7.27: 清除读load计数器

                            if (ddr3_init_done && adc_start_cmd) begin
                                // V8.7.1: 根据trigger_enable决定是否等待触发
                                if (trigger_enable) begin
                                    ddr3_ctrl_state <= DDR3_WAIT_TRIGGER;
                                    trigger_enable_internal <= 1'b1;  // 使能触发检测
                                end
                                else begin
                                    ddr3_ctrl_state <= DDR3_CAPTURING;
                                    wr_load_pulse_cnt <= 5'd1;
                                    ddr3_wr_load_reg <= 1'b1;
                                end
                            end
                        end

                        DDR3_WAIT_TRIGGER: begin
                            // V8.7.1: 等待触发状态
                            if (trigger_detected) begin
                                ddr3_ctrl_state <= DDR3_CAPTURING;
                                wr_load_pulse_cnt <= 5'd1;
                                ddr3_wr_load_reg <= 1'b1;
                                trigger_enable_internal <= 1'b0;  // 禁用触发检测
                            end
                            else if (adc_stop_cmd) begin
                                // 手动停止
                                ddr3_ctrl_state <= DDR3_IDLE;
                                trigger_enable_internal <= 1'b0;
                            end
                        end

                        DDR3_CAPTURING: begin
                            if (wr_load_pulse_cnt > 0 && wr_load_pulse_cnt < 5'd3) begin
                                wr_load_pulse_cnt <= wr_load_pulse_cnt + 1'b1;
                                ddr3_wr_load_reg <= 1'b1;
                            end
                            else if (wr_load_pulse_cnt == 5'd3) begin
                                wr_load_pulse_cnt <= 5'd0;
                                ddr3_wr_load_reg <= 1'b0;
                                ddr3_data_valid <= 1'b1;
                            end
                            else begin
                                ddr3_wr_load_reg <= 1'b0;
                            end

                            // 🔥 V8.7.39修复: 写入计数必须与实际写入条件一致！
                            // 关键：只有真正写入DDR3时才计数（与wfifo_wren条件完全一致）
                            if (adc_data_valid_latched && ddr3_data_valid && !ddr3_wr_fifo_full && !ddr3_almost_full) begin
                                ddr3_write_count <= ddr3_write_count + 1'b1;
                            end

                            // 🔥 V8.7.59修复: 只有ddr3_data_valid=1后才开始计数adc_sample_cnt
                            // 原因：ddr3_data_valid在进入CAPTURING后需要3个周期才置1
                            //       前3个周期ADC产生的数据不会写入DDR3（ddr3_data_valid=0）
                            //       如果这时候adc_sample_cnt就开始计数，会导致计数不匹配
                            // 修复：只有DDR3真正可以接收数据后，才开始统计ADC产生的数据量
                            if (adc_data_16bit_valid && ddr3_data_valid) begin
                                adc_sample_cnt <= adc_sample_cnt + 1'b1;
                            end

                            // 🔥 V8.7.59修复: 50MSPS死锁问题 - 使用adc_sample_cnt而非ddr3_write_count判断完成
                            // 原因：50MSPS时背压频繁触发，ddr3_write_count远小于目标，导致永远达不到完成条件
                            // 修复：用ADC产生的采样数（adc_sample_cnt）判断，而不是实际写入DDR3的数量
                            if (adc_mode == 1'b1 && adc_sample_cnt >= adc_buffer_size) begin
                                ddr3_ctrl_state <= DDR3_WAIT_FIFO_CLEAR;  // 跳转到等待FIFO清空状态
                                ddr3_wr_load_reg <= 1'b0;  // 停止DDR3写入
                                ddr3_data_valid <= 1'b0;   // 禁止新数据写入
                            end
                            else if (adc_stop_cmd) begin
                                ddr3_ctrl_state <= DDR3_WAIT_FIFO_CLEAR;
                                ddr3_wr_load_reg <= 1'b0;
                                ddr3_data_valid <= 1'b0;
                            end
                        end

                        DDR3_WAIT_FIFO_CLEAR: begin
                            // 🔥 V8.7.51新增状态: 等待FIFO清空完成
                            // 这个状态确保ddr3_data_valid=0，不会有新数据写入DDR3
                            // 同时等待eth_fifo复位完成
                            ddr3_wr_load_reg <= 1'b0;
                            ddr3_data_valid <= 1'b0;  // 确保保持禁止状态

                            // 等待FIFO清空完成后跳转到TRANSFERRING
                            if (fifo_clear_done) begin
                                ddr3_ctrl_state <= DDR3_TRANSFERRING;
                                data_ready_flag <= 1'b1;
                                rd_load_pulse_cnt <= 5'd1;
                                ddr3_rd_load_reg <= 1'b1;
                            end
                        end

                        DDR3_TRANSFERRING: begin
                            if (rd_load_pulse_cnt > 0 && rd_load_pulse_cnt < 5'd3) begin
                                rd_load_pulse_cnt <= rd_load_pulse_cnt + 1'b1;
                                ddr3_rd_load_reg <= 1'b1;
                            end
                            else if (rd_load_pulse_cnt == 5'd3) begin
                                rd_load_pulse_cnt <= 5'd0;
                                ddr3_rd_load_reg <= 1'b0;
                                rd_started <= 1'b1;
                                eth_transfer_active <= 1'b1;
                            end
                            else begin
                                ddr3_rd_load_reg <= 1'b0;
                            end

                            if (ddr3_rd_data_valid_stage3) begin
                                ddr3_read_count <= ddr3_read_count + 1'b1;
                            end

                            // 🔥 V8.7.52修复: DDR3读完后，等待16→8转换器完成最后一个数据
                            // 关键：转换器可能在CONV_SEND_HIGH状态，还有高字节没发
                            // 必须等待转换器回到CONV_IDLE才能停止DDR3输出
                            if (ddr3_read_count >= ddr3_write_count && eth_convert_state == CONV_IDLE) begin
                                ddr3_ctrl_state <= DDR3_DONE;
                                eth_transfer_active <= 1'b0;  // 转换完成后才拉低
                                data_ready_flag <= 1'b0;
                                ddr3_rd_load_reg <= 1'b0;
                            end
                            else if (adc_stop_cmd) begin
                                ddr3_ctrl_state <= DDR3_DONE;
                                eth_transfer_active <= 1'b0;
                                data_ready_flag <= 1'b0;
                            end
                        end

                        DDR3_DONE: begin
                            data_ready_flag <= 1'b0;
                            ddr3_wr_load_reg <= 1'b0;
                            ddr3_rd_load_reg <= 1'b0;
                            ddr3_data_valid <= 1'b0;
                            eth_transfer_active <= 1'b0;  // 🔥 确保保持为0

                            // 🔥 V8.7.35修复: 增加超时保护，防止永久卡死
                            // 超时时间: 65535 × 20ns = 1.3ms (足够FIFO发送完毕)
                            done_delay_cnt <= done_delay_cnt + 1'b1;

                            // 🔥 V8.7.9: 等待ETH FIFO中的数据发送完后回IDLE
                            if (eth_fifo_empty || done_delay_cnt >= 16'd65535) begin
                                // 清零所有计数器
                                ddr3_write_count <= 32'd0;
                                ddr3_read_count <= 32'd0;
                                adc_sample_cnt <= 32'd0;
                                done_delay_cnt <= 16'd0;
                                ddr3_ctrl_state <= DDR3_IDLE;
                            end
                            else if (adc_stop_cmd) begin
                                // 强制停止：立即回IDLE（可能丢失数据）
                                ddr3_write_count <= 32'd0;
                                ddr3_read_count <= 32'd0;
                                adc_sample_cnt <= 32'd0;
                                done_delay_cnt <= 16'd0;
                                ddr3_ctrl_state <= DDR3_IDLE;
                            end
                        end

                        default: begin
                            ddr3_ctrl_state <= DDR3_IDLE;
                        end
                    endcase
                end

                default: begin
                    ddr3_wr_load_reg <= 1'b0;
                    ddr3_rd_load_reg <= 1'b0;
                    data_ready_flag <= 1'b1;
                end
            endcase
        end
    end

    assign ddr3_wr_load = ddr3_wr_load_reg;
    assign ddr3_rd_load = ddr3_rd_load_reg;

    //-------------------------------------------------------------------------
    // 数据源选择：流模式直接用ADC，Buffer模式用DDR3
    //-------------------------------------------------------------------------
    wire ddr3_data_output_enable;
    assign ddr3_data_output_enable = (adc_mode == 1'b1) ? (ddr3_ctrl_state == DDR3_TRANSFERRING || ddr3_ctrl_state == DDR3_DONE) : 1'b0;

    // 以太网传输的16位数据流
    wire [15:0] eth_tx_data;
    wire eth_tx_data_valid;

    // 🔥 修复：流模式(adc_mode=0)直接使用ADC交织数据，Buffer模式(adc_mode=1)使用DDR3数据
    assign eth_tx_data = (adc_mode == 1'b0) ? adc_interleaved_data : ddr3_rd_data_stage3;
    assign eth_tx_data_valid = (adc_mode == 1'b0) ? (adc_interleaved_valid && adc_stream_active) :
           (ddr3_rd_data_valid_stage3 && ddr3_data_output_enable);

    //-------------------------------------------------------------------------
    // 逻辑分析仪数据帧编码器（防止丢包，添加帧结构）
    // 注意：暂时禁用帧编码，直接传输原始数据
    //-------------------------------------------------------------------------
    wire [7:0] la_frame_out;
    wire       la_frame_valid;
    wire       la_frame_ready;
    wire       la_encoder_busy;

    // 直接传输模式：绕过帧编码器
    assign la_frame_out = la_fifo_rd_data;
    assign la_frame_valid = la_fifo_wr_en;
    assign la_frame_ready = 1'b1;
    assign la_encoder_busy = 1'b0;

    /* 帧编码器（暂时注释）
    data_frame_encoder #(
        .MAX_PAYLOAD_SIZE(64)  // 每帧最大64字节数据（更快发送）
    ) u_la_frame_encoder (
        .clk            (clk),
        .rst_n          (~sys_rst),
        
        // 输入数据（来自逻辑分析仪）
        .data_in        (la_fifo_rd_data),
        .data_valid     (la_fifo_wr_en),  // LA输出有效信号
        .data_ready     (),               // 背压信号（暂不使用）
        
        // 输出帧数据
        .frame_out      (la_frame_out),
        .frame_valid    (la_frame_valid),
        .frame_ready    (1'b1),           // 暂时固定为1，简化调试
        
        // 控制接口
        .flush          (la_capture_done || la_capture_stop),  // 采集完成时强制发送
        .busy           (la_encoder_busy)
    );
    */

    assign ddr3_rd_fifo_rden = ddr3_rd_fifo_rden_stage1;

    //-------------------------------------------------------------------------
    // USB CDC数据源（仅用于逻辑分析仪，ADC已改走以太网）
    //-------------------------------------------------------------------------
    wire [7:0] usb_tx_data_8bit_mux;
    wire usb_tx_data_valid_mux;

    assign usb_tx_data_8bit_mux = la_frame_out;       // USB CDC只传输LA数据
    assign usb_tx_data_valid_mux = la_frame_valid;    // 无需ADC/LA互斥，ADC走以太网

    assign ddr3_rd_fifo_rden = ddr3_rd_fifo_rden_stage1;

    //-------------------------------------------------------------------------
    // LA独立的USB传输使能信号（跨时钟域同步：50MHz → 48MHz）
    //-------------------------------------------------------------------------
    reg la_usb_enable_fx2_sync1;
    reg la_usb_enable_fx2_sync2;
    reg la_read_enable_fx2;  // LA的读使能信号（在fx2时钟域）
    reg la_capture_done_fx2_sync1;  // LA采集完成同步
    reg la_capture_done_fx2_sync2;
    reg la_tx_complete_fx2;  // LA传输完成标志（fx2时钟域）
    reg [15:0] la_empty_counter;  // FIFO空计数器

    always @(posedge clk_fx2 or posedge sys_rst) begin
        if (sys_rst) begin
            la_usb_enable_fx2_sync1 <= 1'b0;
            la_usb_enable_fx2_sync2 <= 1'b0;
            la_capture_done_fx2_sync1 <= 1'b0;
            la_capture_done_fx2_sync2 <= 1'b0;
            la_read_enable_fx2 <= 1'b0;
            la_tx_complete_fx2 <= 1'b0;
            la_empty_counter <= 16'd0;
        end
        else begin
            // 两级同步器
            la_usb_enable_fx2_sync1 <= la_usb_enable;
            la_usb_enable_fx2_sync2 <= la_usb_enable_fx2_sync1;
            la_capture_done_fx2_sync1 <= la_capture_done;
            la_capture_done_fx2_sync2 <= la_capture_done_fx2_sync1;

            // 🔥 关键修复：LA读使能直接由la_usb_enable控制（LA独占CDC通道）
            // 注意：由于架构变更（ADC走以太网，LA走CDC），不需要互斥检查
            la_read_enable_fx2 <= la_usb_enable_fx2_sync2;

            // LA传输完成检测：采集完成 + FIFO空 + 一段时间无新数据
            if (!la_usb_enable_fx2_sync2) begin
                la_tx_complete_fx2 <= 1'b0;
                la_empty_counter <= 16'd0;
            end
            else if (la_capture_done_fx2_sync2 && la_tx_empty && (la_tx_data_count == 17'd0)) begin  // 🔥 修复：改为17位比较
                // FIFO空且无数据时，开始计数
                if (la_empty_counter < 16'd1000) begin  // 约20ms @ 48MHz
                    la_empty_counter <= la_empty_counter + 1'b1;
                end
                else begin
                    la_tx_complete_fx2 <= 1'b1;  // 传输完成
                end
            end
            else begin
                la_empty_counter <= 16'd0;
            end
        end
    end

    // 传输完成信号同步回50MHz域（仅用于检测，不直接清除la_usb_enable）
    reg la_tx_complete_sync1;
    reg la_tx_complete_sync2;
    wire la_tx_complete_event;  // 传输完成事件（单周期脉冲）

    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            la_tx_complete_sync1 <= 1'b0;
            la_tx_complete_sync2 <= 1'b0;
        end
        else begin
            la_tx_complete_sync1 <= la_tx_complete_fx2;
            la_tx_complete_sync2 <= la_tx_complete_sync1;
        end
    end

    // 边沿检测：产生单周期脉冲
    reg la_tx_complete_sync2_d1;
    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            la_tx_complete_sync2_d1 <= 1'b0;
        end
        else begin
            la_tx_complete_sync2_d1 <= la_tx_complete_sync2;
        end
    end
    assign la_tx_complete_event = la_tx_complete_sync2 && !la_tx_complete_sync2_d1;

    //-------------------------------------------------------------------------
    // 🔥 V9.0重命名：LA数据缓冲FIFO（异步FIFO：sys_clk → fx2_ifclk）
    // 注：历史上命名为adc_tx_*，但实际只用于逻辑分析仪（ADC已改走以太网）
    // 🔥 修复：确保FIFO清空信号维持足够长的时间（至少2个时钟周期）
    //-------------------------------------------------------------------------
    wire [7:0]  la_tx_data;
    wire        la_tx_empty;
    wire        la_tx_almost_empty;
    wire        la_tx_almost_full;
    wire [16:0] la_tx_data_count;   // 🔥 修复：改为17位匹配IP核输出（支持65536深度）
    wire        la_tx_rd_req;
    wire        la_tx_pkt_end;

    // 🔥 关键修复：FIFO阈值机制，防止频繁空/满切换
    wire fifo_ready_to_read = (la_tx_data_count > 17'd512);  // 🔥 修复：改为17位比较

    // 🔥 关键修复：连接FX2读请求信号到FIFO读使能
    assign la_tx_rd_req = tx_rd_req;  // 使用FX2的读请求信号

    // 🔥 FIFO清空信号扩展：单周期脉冲扩展为多周期复位信号
    reg [3:0] fifo_clear_cnt;
    reg fifo_clear_extended;

    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            fifo_clear_cnt <= 4'd0;
            fifo_clear_extended <= 1'b0;
        end
        else begin
            if (la_fifo_clear) begin
                // LA清空请求到来，开始扩展复位脉冲
                fifo_clear_cnt <= 4'd10;  // 维持10个时钟周期（200ns @ 50MHz）
                fifo_clear_extended <= 1'b1;
            end
            else if (fifo_clear_cnt > 0) begin
                // 计数器递减
                fifo_clear_cnt <= fifo_clear_cnt - 1'b1;
                fifo_clear_extended <= 1'b1;
            end
            else begin
                // 计数器归零，清除复位信号
                fifo_clear_extended <= 1'b0;
            end
        end
    end

    // 🔥 修复：连接FIFO的Almost_Full和Almost_Empty输出
    wire la_fifo_almost_full;   // FIFO接近满标志（从FIFO IP核输出）
    wire la_fifo_almost_empty;  // FIFO接近空标志（从FIFO IP核输出）
    assign la_tx_almost_full = la_fifo_almost_full;     // 使用FIFO IP核的Almost_Full
    assign la_tx_almost_empty = la_fifo_almost_empty;   // 使用FIFO IP核的Almost_Empty

    fifo_top u_la_tx_fifo(
                 .Data         (usb_tx_data_8bit_mux),          // 多路选择后的数据
                 .Reset        (sys_rst || fifo_clear_extended),  // 🔥 使用扩展后的清空信号
                 .WrClk        (clk),
                 .RdClk        (clk_fx2),
                 .WrEn         (usb_tx_data_valid_mux && !la_tx_almost_full && !fifo_clear_extended),  // 🔥 清空期间禁止写入
                 .RdEn         (la_tx_rd_req && la_read_enable_fx2), // LA使能时读取（ADC已走以太网）
                 .Rnum         (la_tx_data_count),
                 .Almost_Full  (la_fifo_almost_full),   // 🔥 修复：连接Almost_Full输出
                 .Almost_Empty (la_fifo_almost_empty),  // 🔥 修复：连接Almost_Empty输出
                 .Q            (la_tx_data),
                 .Empty        (la_tx_empty),
                 .Full         (la_fifo_full)
             );

    //=========================================================================
    // 以太网UDP数据包发送模块（使用例程的稳定架构）
    //=========================================================================

    //-------------------------------------------------------------------------
    // ADC数据FIFO（50MHz → 125MHz异步FIFO）
    //-------------------------------------------------------------------------
    wire [7:0]  eth_fifo_data;
    wire        eth_fifo_empty;
    wire [15:0] eth_fifo_data_count;  // 🔥 V7.2: 扩展到16位支持65536深度FIFO
    wire        eth_fifo_rd_en;
    wire        eth_fifo_wr_en;
    // 注意：eth_fifo_full已在Line 2255提前声明，供interleaver背压控制使用

    // 🔥 V6.2根治方案：16-to-8转换状态机（彻底消除相位随机性）
    // 核心思路：
    // 1. 系统复位时：强制从IDLE开始
    // 2. adc_stop_cmd时：立即回到IDLE（确保下次启动相位正确）
    // 3. 正常运行：严格按照 IDLE→LOW→HIGH→IDLE 循环
    // 4. FIFO满时：保持当前状态，等FIFO空出来继续
    reg [7:0] eth_fifo_wr_data;
    reg [1:0] eth_convert_state;
    reg [15:0] eth_data_buffer;
    reg eth_fifo_wr_en_reg;

    localparam CONV_IDLE      = 2'd0;
    localparam CONV_SEND_LOW  = 2'd1;
    localparam CONV_SEND_HIGH = 2'd2;

    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            eth_convert_state <= CONV_IDLE;
            eth_fifo_wr_data <= 8'd0;
            eth_data_buffer <= 16'd0;
            eth_fifo_wr_en_reg <= 1'b0;
        end
        else begin
            // 🔥 V6.2关键修复：停止采集时立即回到IDLE，确保下次启动相位正确
            // 🔥 V8.7.44修复：Buffer模式在回到DDR3_IDLE时也复位转换状态机
            if (adc_stop_cmd ||
                    (adc_mode == 1'b1 && ddr3_ctrl_state == DDR3_IDLE && ddr3_ctrl_state_d1 != DDR3_IDLE)) begin
                eth_convert_state <= CONV_IDLE;
                eth_fifo_wr_en_reg <= 1'b0;
                // eth_data_buffer保持不变（避免丢失最后的数据）
            end
            else begin
                case (eth_convert_state)
                    CONV_IDLE: begin
                        eth_fifo_wr_en_reg <= 1'b0;

                        // 等待新的16位数据到来
                        if (eth_tx_data_valid && !eth_fifo_full) begin
                            eth_data_buffer <= eth_tx_data;  // 缓存16位数据
                            eth_fifo_wr_data <= eth_tx_data[7:0];  // 🔥 立即准备低字节
                            eth_fifo_wr_en_reg <= 1'b1;  // 🔥 立即写入低字节
                            eth_convert_state <= CONV_SEND_HIGH;  // 🔥 跳过SEND_LOW，直接到SEND_HIGH
                        end
                    end

                    CONV_SEND_LOW: begin
                        // 🔥 V8.6.28: 此状态已不使用（直接从IDLE写低字节）
                        eth_fifo_wr_en_reg <= 1'b0;
                        eth_convert_state <= CONV_IDLE;
                    end

                    CONV_SEND_HIGH: begin
                        // 🔥 V8.6.28修复：写入高字节（低字节已在IDLE时写入）
                        if (!eth_fifo_full) begin
                            eth_fifo_wr_data <= eth_data_buffer[15:8];
                            eth_fifo_wr_en_reg <= 1'b1;
                            eth_convert_state <= CONV_IDLE;
                        end
                        else begin
                            // FIFO满，清除wr_en并保持状态
                            eth_fifo_wr_en_reg <= 1'b0;
                        end
                    end

                    default: begin
                        eth_convert_state <= CONV_IDLE;
                        eth_fifo_wr_en_reg <= 1'b0;
                    end
                endcase
            end
        end
    end

    assign eth_fifo_wr_en = eth_fifo_wr_en_reg;

    //-------------------------------------------------------------------------
    // 🔥 V8.7.27: 统一FIFO架构 - 流模式和Buffer模式共用fifo_top (FWFT)
    //-------------------------------------------------------------------------
    // 两种模式都使用相同的协议格式: 16字节头 + N字节数据
    // 流模式: 16头 + 1008数据 = 1024字节 (504个采样点)
    // Buffer模式: 16头 + 1024数据 = 1040字节 (512个采样点)

    // 🔥 V8.7.50关键修复: Buffer模式FIFO清除时机改进
    // 策略: CAPTURING结束时立即清除FIFO，DDR3读取前等待FIFO清空完成
    // 时序: CAPTURING完成 → 清FIFO(10周期) → rd_load(3周期) → 开始读DDR3
    // 这样确保DDR3数据写入的FIFO是干净的，没有上次采集的残留数据
    reg eth_fifo_reset_buffer;
    reg [4:0] fifo_reset_cnt;  // 🔥 扩展到5位，支持20个周期
    reg [2:0] ddr3_ctrl_state_d1;  // 🔥 修复：改为3位以匹配ddr3_ctrl_state
    reg fifo_clear_request;  // FIFO清除请求标志

    always @(posedge clk or posedge sys_rst) begin
        if (sys_rst) begin
            eth_fifo_reset_buffer <= 1'b0;
            ddr3_ctrl_state_d1 <= DDR3_IDLE;
            fifo_reset_cnt <= 5'd0;
            fifo_clear_request <= 1'b0;
        end
        else begin
            ddr3_ctrl_state_d1 <= ddr3_ctrl_state;

            if (adc_mode == 1'b1) begin
                // 🔥 V8.7.51核心修复: 检测进入WAIT_FIFO_CLEAR状态
                if ((ddr3_ctrl_state == DDR3_WAIT_FIFO_CLEAR && ddr3_ctrl_state_d1 != DDR3_WAIT_FIFO_CLEAR) ||
                        adc_stop_cmd ||
                        (ddr3_ctrl_state == DDR3_IDLE && ddr3_ctrl_state_d1 != DDR3_IDLE)) begin
                    // 进入WAIT_FIFO_CLEAR或停止命令：立即清除FIFO
                    fifo_reset_cnt <= 5'd20;  // 20个时钟周期 = 400ns @ 50MHz
                    eth_fifo_reset_buffer <= 1'b1;
                    fifo_clear_request <= 1'b1;
                end
                else if (fifo_reset_cnt > 0) begin
                    // 递减计数器
                    fifo_reset_cnt <= fifo_reset_cnt - 1'b1;
                    eth_fifo_reset_buffer <= 1'b1;
                end
                else begin
                    // 计数器归零，清除复位信号
                    eth_fifo_reset_buffer <= 1'b0;
                    if (fifo_clear_request) begin
                        fifo_clear_request <= 1'b0;  // 清除完成
                    end
                end
            end
            else begin
                eth_fifo_reset_buffer <= 1'b0;
                fifo_reset_cnt <= 5'd0;
                fifo_clear_request <= 1'b0;
            end
        end
    end
    wire eth_fifo_reset = sys_rst || eth_fifo_reset_buffer;
    wire fifo_clear_done = (fifo_reset_cnt == 5'd0) && !fifo_clear_request;  // FIFO清除完成标志

    fifo_top u_eth_adc_fifo (
                 .Data         (eth_fifo_wr_data),
                 .Reset        (eth_fifo_reset),  // 🔥 V8.7.27: Buffer模式启动时清除
                 .WrClk        (clk),
                 .RdClk        (clk125m_eth),
                 .WrEn         (eth_fifo_wr_en),
                 .RdEn         (eth_fifo_rd_en),
                 .Rnum         (eth_fifo_data_count),
                 .Q            (eth_fifo_data),
                 .Empty        (eth_fifo_empty),
                 .Full         (eth_fifo_full)
             );

    //-------------------------------------------------------------------------
    // Buffer模式控制器 (V8.7.0) - 暂时禁用，需要适配现有DDR3接口
    //-------------------------------------------------------------------------
    // 注意：buffer_mode_controller需要重新设计以适配现有的ddr3_ctrl_2port接口
    // 当前的DDR3控制器使用FIFO接口而不是直接地址访问
    // TODO: 创建buffer模式适配器或修改buffer_mode_controller接口

    /*
    buffer_mode_controller u_buffer_mode_ctrl (
                               .clk                  (clk),
                               .rst_n                (~sys_rst),
                               .cmd_start            (adc_mode && adc_start_cmd),
                               .cmd_stop             (adc_stop_cmd),
                               .cfg_depth            (adc_buffer_size),
                               .cfg_trig_level       ({buffer_trigger_level, 8'd0}), // 转换8位到16位
                               .cfg_trig_edge        (buffer_trigger_edge),
                               .cfg_trig_mode        (2'b00), // 预留
                               // ADC数据需要8位转16位
                               .adc_ch1_data         ({adc_ch1_data, 8'd0}),
                               .adc_ch2_data         ({adc_ch2_data, 8'd0}),
                               .adc_valid            (adc_interleaved_valid),
                               // DDR3接口不兼容 - 需要适配层
                               .ddr3_wr_addr         (),
                               .ddr3_wr_data         (),
                               .ddr3_wr_en           (),
                               .ddr3_wr_ready        (1'b1),
                               .ddr3_rd_addr         (),
                               .ddr3_rd_en           (),
                               .ddr3_rd_data         (128'd0),
                               .ddr3_rd_valid        (1'b0),
                               // UDP接口不兼容 - 需要适配层
                               .udp_packet           (),
                               .udp_packet_valid     (),
                               .udp_packet_ready     (1'b0),
                               .status_byte          (buffer_status_byte),
                               .progress_count       (buffer_progress_count)
                           );
    */

    // 临时处理：将Buffer模式状态信号连接到实际状态
    // 状态字节格式：[7:5]保留 [4:2]状态码 [1]触发标志 [0]忙标志
    // 状态码: 0=IDLE, 1=WAIT_TRIG, 2=CAPTURING, 3=WAIT_FIFO_CLEAR, 4=TRANSFERRING, 5=DONE
    assign buffer_status_byte = {3'b000, ddr3_ctrl_state, trigger_detected, (ddr3_ctrl_state != DDR3_IDLE)};
    assign buffer_progress_count = (ddr3_ctrl_state == DDR3_CAPTURING) ? ddr3_write_count :
           (ddr3_ctrl_state == DDR3_TRANSFERRING) ? ddr3_read_count :
           32'd0;
    assign buffer_mode_active = (adc_mode == 1'b1) && (ddr3_ctrl_state != DDR3_IDLE);

    //-------------------------------------------------------------------------
    // ADC以太网发送控制器（例程的稳定模块）
    //-------------------------------------------------------------------------
    wire        eth_tx_en_pulse;
    wire        eth_payload_req;
    wire [7:0]  eth_payload_data;
    wire [2:0]  eth_state_debug;

    adc_eth_tx_controller u_adc_eth_tx_ctrl (
                              .clk125M            (clk125m_eth),
                              .rst_n              (~sys_rst),

                              // FIFO接口
                              .fifo_data          (eth_fifo_data),
                              .fifo_empty         (eth_fifo_empty),
                              .fifo_data_count    (eth_fifo_data_count),
                              .fifo_rd_en         (eth_fifo_rd_en),

                              // UDP模块接口
                              .tx_en_pulse        (eth_tx_en_pulse),
                              .tx_done            (eth_tx_done),
                              .payload_req        (eth_payload_req),
                              .payload_data       (eth_payload_data),

                              // 🔥 V8.7.2修复: Buffer模式使用eth_transfer_active控制UDP发送
                              // 流模式: adc_stream_active
                              // Buffer模式: eth_transfer_active (DDR3_TRANSFERRING状态置1)
                              .adc_stream_active  (adc_stream_active || eth_transfer_active),
                              .ch1_enable         (ch1_enable),  // 🔥 V7.1修复：连接到正确的信号
                              .ch2_enable         (ch2_enable),  // 🔥 V7.1修复：连接到正确的信号

                              // 🔥 V8.7.10新增：Buffer模式控制
                              .adc_mode           (adc_mode),              // 0=Stream, 1=Buffer
                              .total_samples      (adc_buffer_size),       // 🔥 V8.7.41修复: 使用配置值而非实际值
                              // 注意：原来用ddr3_write_count会导致计算时机错误（采集未完成时就计算）
                              //      改用adc_buffer_size（用户配置的目标深度），确保每次都一致

                              // 调试
                              .packet_count       (eth_packet_count),
                              .state_debug        (eth_state_debug)
                          );

    //-------------------------------------------------------------------------
    // UDP/IP/Ethernet封装模块（例程的稳定wrapper）
    //-------------------------------------------------------------------------
    // 🔥 V8.7.24: 根据模式动态选择包长度
    wire [15:0] eth_packet_length = adc_mode ? 16'd1040 : 16'd1024;  // Buffer=1040, Stream=1024

    eth_udp_tx_wrapper u_eth_udp_wrapper (
                           .clk125M        (clk125m_eth),
                           .rst_n          (~sys_rst),

                           // 控制接口
                           .tx_en_pulse    (eth_tx_en_pulse),
                           .tx_done        (eth_tx_done),
                           .data_length    (eth_packet_length),  // 🔥 V8.7.24: 动态包长度

                           // 数据接口
                           .payload_req    (eth_payload_req),
                           .payload_data   (eth_payload_data),

                           // RGMII物理接口
                           .rgmii_tx_clk   (rgmii_tx_clk),
                           .rgmii_txd      (rgmii_txd),
                           .rgmii_txen     (rgmii_txen)
                       );

    //-------------------------------------------------------------------------
    // 连接逻辑分析仪数据到FX2 CDC发送通道（USB CDC仅用于LA）
    // 🔥 关键修复：必须从异步FIFO输出读取，而不是直连50MHz时钟域信号
    //-------------------------------------------------------------------------
    // 数据源：异步FIFO的输出（已经跨时钟域到FX2时钟）
    assign tx_data  = la_tx_data;  // 🔥 V9.0: 重命名为la_tx_data，反映实际用途

    // 🔥 关键修复：完整的tx_empty逻辑（参考LOGIC版本）
    assign tx_empty = la_tx_empty
           || !fifo_ready_to_read  // FIFO数据不足时阻止USB读取
           || !la_read_enable_fx2;  // LA未使能时阻止读取（ADC已走以太网）

    //=========================================================================
    // 功能扩展区域 - 待后续阶段实现
    //=========================================================================

    // ✅ 阶段2 - 示波器功能（已实现）
    // - ADC采样控制
    // - DDR3数据缓存
    // - USB数据回传

    // ✅ 阶段3 - 函数发生器（已实现）
    // - DDS波形生成（5种波形）
    // - 双通道DAC输出控制
    // - 频率/相位/幅度可调

    // TODO: 阶段5 - 协议控制器
    // - I2C/SPI/UART/PWM驱动

    // TODO: 阶段6 - 设备中心
    // - OLED/Flash/蓝牙/电机控制

endmodule
