// 
// Copyright 2011-2012 Jeff Bush
// 
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
// 
//     http://www.apache.org/licenses/LICENSE-2.0
// 
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
// 

module display_controller(
	input 			clk,
	output			hsync_o,
	output			vsync_o,
	output [3:0]	red_o,
	output [3:0]	green_o,
	output [3:0]	blue_o,
	input			register_write_i,
	input [11:0]	register_index_i,
	input [15:0]	register_write_value_i,
	output			in_vblank_o,
	output reg[5:0]	collision_o);

	wire[9:0] raster_x_native;
	wire[9:0] raster_y_native;
	wire[9:0] raster_x;
	wire[9:0] raster_y;
	wire in_visible_region;
	wire[11:0] 	sprite0_address;
	wire[11:0] 	sprite1_address;
	wire[11:0] 	sprite2_address;
	wire[11:0] 	sprite3_address;
	wire sprite0_active;
	wire sprite1_active;
	wire sprite2_active;
	wire sprite3_active;
	
	vga_timing_generator vtg(
		.clk(clk), 
		.vsync_o(vsync_o), 
		.hsync_o(hsync_o), 
		.in_visible_region(in_visible_region),
		.x_coord(raster_x_native), 
		.y_coord(raster_y_native),
		.in_vblank(in_vblank_o));
		
	// Cut the resolution by half in each dimension
	assign raster_x = { 1'b0, raster_x_native[9:1] };
	assign raster_y = { 1'b0, raster_y_native[9:1] };

	sprite_detect #(3) sprite0(
		.clk(clk),
		.raster_x(raster_x),
		.raster_y(raster_y),
		.register_write_i(register_write_i),
		.register_index_i(register_index_i),
		.register_write_value_i(register_write_value_i),
		.sprite_active(sprite0_active),
		.sprite_address(sprite0_address));

	sprite_detect #(7) sprite1(
		.clk(clk),
		.raster_x(raster_x),
		.raster_y(raster_y),
		.register_write_i(register_write_i),
		.register_index_i(register_index_i),
		.register_write_value_i(register_write_value_i),
		.sprite_active(sprite1_active),
		.sprite_address(sprite1_address));

	sprite_detect #(11) sprite2(
		.clk(clk),
		.raster_x(raster_x),
		.raster_y(raster_y),
		.register_write_i(register_write_i),
		.register_index_i(register_index_i),
		.register_write_value_i(register_write_value_i),
		.sprite_active(sprite2_active),
		.sprite_address(sprite2_address));

	sprite_detect #(14) sprite3(
		.clk(clk),
		.raster_x(raster_x),
		.raster_y(raster_y),
		.register_write_i(register_write_i),
		.register_index_i(register_index_i),
		.register_write_value_i(register_write_value_i),
		.sprite_active(sprite3_active),
		.sprite_address(sprite3_address));

	// Sprite priority logic	
	reg[11:0] sprite_addr = 0;	// Sprite shape + address
	always @*
	begin
		if (sprite0_active)
			sprite_addr = sprite0_address;
		else if (sprite1_active)
			sprite_addr = sprite1_address;
		else if (sprite2_active)
			sprite_addr = sprite2_address;
		else /* sprite3 active or don't care */
			sprite_addr = sprite3_address;
	end

	reg[5:0] collision = 0;
	reg in_vblank_latched = 0;
	
	always @(posedge clk)
	begin
		in_vblank_latched <= in_vblank_o;
		
		if (in_vblank_o & ~in_vblank_latched)
		begin
			// Start of frame
			collision <= 0;
			collision_o <= collision;
		end
		else
		begin
			collision <= collision | { 
				sprite0_active & sprite1_active,
				sprite0_active & sprite2_active,
				sprite0_active & sprite3_active,
				sprite1_active & sprite2_active,
				sprite1_active & sprite3_active,
				sprite2_active & sprite3_active
			};
		end	
	end
	

	wire[11:0] sprite_color;
	wire[3:0] sprite_color_index;

	arom #(4096, 4, 12, "sprites.hex") sprite_rom(
		.addr_i(sprite_addr),
		.value_o(sprite_color_index));
		
	arom #(16, 12, 4, "palette.hex") palette_rom(
		.addr_i(sprite_color_index),
		.value_o(sprite_color));
		
	wire sprite_is_active = sprite0_active || sprite1_active;

	reg[11:0] output_color = 0;
	assign { red_o, green_o, blue_o } = output_color;
	always @*
	begin
		if (in_visible_region)
		begin
			if (sprite_is_active && sprite_color_index != 0)
				output_color = sprite_color;
			else
				output_color = 12'b000000000100;	// Background color
		end
		else
			output_color = 12'd0;
	end
endmodule
