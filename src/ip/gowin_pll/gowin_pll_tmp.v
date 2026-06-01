//Copyright (C)2014-2025 Gowin Semiconductor Corporation.
//All rights reserved.
//File Title: Template file for instantiation
//Part Number: GW5AT-LV138PG484AC1/I0
//Device: GW5AT-138
//Device Version: B


//Change the instance name and port connections to the signal names
//--------Copy here to design--------
    Gowin_PLL your_instance_name(
        .clkin(clkin), //input  clkin
        .init_clk(init_clk), //input  init_clk
        .clkout0(clkout0), //output  clkout0
        .clkout1(clkout1), //output  clkout1
        .lock(lock), //output  lock
        .reset(reset) //input  reset
);


//--------Copy end-------------------
