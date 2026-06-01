//=============================================================================
// 以太网UDP发送封装模块
// 功能：封装eth_udp_tx_gmii和gmii_to_rgmii，简化顶层连接
// 版本：V1.0
// 日期：2025-11-18
//=============================================================================

module eth_udp_tx_wrapper (
    input  wire         clk125M,            // 125MHz以太网时钟
    input  wire         rst_n,              // 复位信号
    
    // ADC数据接口
    input  wire         tx_en_pulse,        // 发送触发
    output wire         tx_done,            // 发送完成
    input  wire [15:0]  data_length,        // 数据长度
    output wire         payload_req,        // 数据请求
    input  wire [7:0]   payload_data,       // 数据输入
    
    // RGMII物理接口
    output wire         rgmii_tx_clk,       // RGMII发送时钟
    output wire [3:0]   rgmii_txd,          // RGMII发送数据
    output wire         rgmii_txen          // RGMII发送使能
);

    //=========================================================================
    // 固定以太网参数
    //=========================================================================
    parameter DST_MAC   = 48'hFF_FF_FF_FF_FF_FF; // 目的MAC（广播）
    parameter SRC_MAC   = 48'h00_0A_35_01_FE_C0; // 源MAC
    parameter DST_IP    = 32'hC0_A8_00_03;       // 目的IP：192.168.0.3
    parameter SRC_IP    = 32'hC0_A8_00_02;       // 源IP：192.168.0.2
    parameter DST_PORT  = 16'd6102;              // 目的端口：6102
    parameter SRC_PORT  = 16'd5000;              // 源端口：5000
    
    //=========================================================================
    // GMII信号
    //=========================================================================
    wire        gmii_tx_clk;
    wire [7:0]  gmii_txd;
    wire        gmii_txen;
    
    //=========================================================================
    // UDP/IP协议栈模块
    //=========================================================================
    eth_udp_tx_gmii u_eth_udp_tx_gmii (
        .clk125m        (clk125M),
        .reset_p        (~rst_n),
        
        .tx_en_pulse    (tx_en_pulse),
        .tx_done        (tx_done),
        
        .dst_mac        (DST_MAC),
        .src_mac        (SRC_MAC),
        .dst_ip         (DST_IP),
        .src_ip         (SRC_IP),
        .dst_port       (DST_PORT),
        .src_port       (SRC_PORT),
        
        .data_length    (data_length),
        
        .payload_req_o  (payload_req),
        .payload_dat_i  (payload_data),
        
        .gmii_tx_clk    (gmii_tx_clk),
        .gmii_txen      (gmii_txen),
        .gmii_txd       (gmii_txd)
    );
    
    //=========================================================================
    // GMII转RGMII模块
    //=========================================================================
    gmii_to_rgmii u_gmii_to_rgmii (
        .reset_n        (rst_n),
        
        .gmii_tx_clk    (gmii_tx_clk),
        .gmii_txd       (gmii_txd),
        .gmii_txen      (gmii_txen),
        .gmii_txer      (1'b0),
        
        .rgmii_tx_clk   (rgmii_tx_clk),
        .rgmii_txd      (rgmii_txd),
        .rgmii_txen     (rgmii_txen)
    );

endmodule
