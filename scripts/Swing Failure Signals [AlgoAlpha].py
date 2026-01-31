// This Pine Script® code is subject to the terms of the Mozilla Public License 2.0 at https://mozilla.org/MPL/2.0/
// © AlgoAlpha

//@version=6
indicator("Swing Failure Signals [AlgoAlpha]", "AlgoAlpha - Swing Failure", overlay = true)

// --- Inputs ---
len = input.int(12, "Pivot Detection Length", tooltip = "The lookback window of the swing high/low detection", group = "Calculations")
len_ = input.int(50, "Max Pivot Point Edge", tooltip = "Max history when looking for swing sweeps", group = "Calculations")
patience = input.int(7, "Patience", tooltip = "The maximum bars to wait for a change in direction after a swing has been sweeped", group = "Calculations")
tolerence = input.float(0.7, "Trend Noise Filter", tooltip = "The maximum bars to wait for a change in direction after a swing has been sweeped", group = "Calculations")

bullCol = input.color(#00ffbb, title = "Bullish Colour", group = "Appearance")
bearCol = input.color(#ff1100, title = "Bearish Colour", group = "Appearance")

// --- Pivot Calculation ---
pivh = ta.pivothigh(len, len)
pivl = ta.pivotlow(len, len)

// --- Arrays ---
var pivhs = array.new_float()
var pivls = array.new_float()
var temp_bull = array.new_float()
var temp_bear = array.new_float()

// --- State Variables ---
var int bar_sweep_bull = 0
var int bar_sweep_bear = 0
var float cisd_level = na
var int cisd_idx = na

bullsfp = false
bearsfp = false

// --- Array Population ---
if not na(pivh)
    pivhs.unshift(pivh)
    pivhs.unshift(bar_index)

if not na(pivl)
    pivls.unshift(pivl)
    pivls.unshift(bar_index)

// --- Array Maintenance ---
while pivhs.size() > 100
    pivhs.pop()
    pivhs.pop()
while pivls.size() > 100
    pivls.pop()
    pivls.pop()

while temp_bull.size() > 3
    temp_bull.pop()
while temp_bear.size() > 3
    temp_bear.pop()

// --- Sweep Detection (Bearish) ---
if pivhs.size() > 1
    lvl = 0.0
    for i = pivhs.size() - 1 to 1 by 2
        if i < pivhs.size()
            pivot_price = pivhs.get(i)
            pivot_idx = pivhs.get(i-1)
            
            if high > pivot_price
                if bar_index - pivot_idx < len_
                    if pivot_price > lvl 
                        lvl := pivot_price
                    
                    temp_bear.unshift(bar_index)
                    temp_bear.unshift(pivot_price)
                    temp_bear.unshift(pivot_idx)
                
                pivhs.remove(i)
                pivhs.remove(i-1)
    
    if lvl != 0.0
        bar_sweep_bear := bar_index

// --- Sweep Detection (Bullish) ---
if pivls.size() > 1
    lvl = 0.0
    for i = pivls.size() - 1 to 1 by 2
        if i < pivls.size()
            pivot_price = pivls.get(i)
            pivot_idx = pivls.get(i-1)
            
            if low < pivot_price
                if bar_index - pivot_idx < len_
                    if (pivot_price < lvl or lvl == 0) 
                        lvl := pivot_price
                    
                    temp_bull.unshift(bar_index)
                    temp_bull.unshift(pivot_price)
                    temp_bull.unshift(pivot_idx)
                
                pivls.remove(i)
                pivls.remove(i-1)

    if lvl != 0.0
        bar_sweep_bull := bar_index

// --- CISD (Change in State of Delivery) Logic ---
var bear_potential = array.new_float()
var bull_potential = array.new_float()

if close[1] < open[1] and close > open
    bear_potential.unshift(bar_index)
    bear_potential.unshift(open)

if close[1] > open[1] and close < open
    bull_potential.unshift(bar_index)
    bull_potential.unshift(open)

cisd = 0

if bear_potential.size() > 0
    inloop = true
    while inloop and bear_potential.size() > 0
        p_idx = bear_potential.get(1) 
        p_val = bear_potential.first() 
        
        if close < p_val
            highest = 0.0
            len_check = bar_index - int(p_idx)
            if len_check >= 0 and len_check < 4999
                for i = 0 to len_check
                    if close[i] > highest
                        highest := close[i]
                
                running = true
                init = len_check + 1
                top = 0.0
                
                while running and init < 4999
                    if close[init] < open[init]
                        top := open[init]
                        init += 1
                    else
                        running := false
                
                denom = top - p_val
                if denom != 0 and (highest - p_val) / denom > tolerence
                    cisd_level := p_val
                    cisd_idx := int(p_idx)
                    bear_potential.clear()
                    cisd := 1 
                    inloop := false
                else
                    bear_potential.shift()
                    bear_potential.shift()
            else 
                bear_potential.shift()
                bear_potential.shift()
        else
            inloop := false

if bull_potential.size() > 0
    inloop = true
    while inloop and bull_potential.size() > 0
        p_idx = bull_potential.get(1)
        p_val = bull_potential.first()
        
        if close > p_val
            lowest = close
            len_check = bar_index - int(p_idx)
            
            if len_check >= 0 and len_check < 4999
                for i = 0 to len_check
                    if close[i] < lowest
                        lowest := close[i]
                
                running = true
                init = len_check + 1
                bottom = 0.0
                
                while running and init < 4999
                    if close[init] > open[init]
                        bottom := open[init]
                        init += 1
                    else
                        running := false
                
                denom = p_val - bottom
                if denom != 0 and (p_val - lowest) / denom > tolerence
                    cisd_level := p_val
                    cisd_idx := int(p_idx)
                    bull_potential.clear()
                    cisd := 2 
                    inloop := false
                else
                    bull_potential.shift()
                    bull_potential.shift()
            else
                bull_potential.shift()
                bull_potential.shift()
        else
            inloop := false

// --- Signal Generation & Plotting ---
var trend = 0
trend := cisd == 1 ? -1 : cisd == 2 ? 1 : trend       

if ta.crossover(trend, 0) and (bar_index - bar_sweep_bull < patience)
    bullsfp := true

if ta.crossunder(trend, 0) and (bar_index - bar_sweep_bear < patience)
    bearsfp := true

// --- Drawing Lines ---
if bullsfp 
    if temp_bull.size() >= 3
        lx = int(temp_bull.get(0)) 
        ly = temp_bull.get(1)      
        
        line_start = lx - len
        line_end = int(temp_bull.get(2)) 
        
        line.new(line_start, ly, line_end, ly, color = bullCol, width = 1, style = line.style_dashed)
        
        mid_x = int((line_start + line_end) / 2)
        label.new(mid_x, ly, text = "$", color = color.new(bullCol, 100), textcolor = bullCol, style = label.style_label_up)

    if not na(cisd_idx) 
        line.new(cisd_idx, cisd_level, bar_index, cisd_level, color = bullCol, width = 1, style = line.style_solid)

if bearsfp 
    if temp_bear.size() >= 3
        lx = int(temp_bear.get(0)) 
        ly = temp_bear.get(1)      
        
        line_start = lx - len
        line_end = int(temp_bear.get(2)) 
        
        line.new(line_start, ly, line_end, ly, color = bearCol, width = 1, style = line.style_dashed)
        
        mid_x = int((line_start + line_end) / 2)
        label.new(mid_x, ly, text = "$", color = color.new(bearCol, 100), textcolor = bearCol, style = label.style_label_down)
        
    if not na(cisd_idx)
        line.new(cisd_idx, cisd_level, bar_index, cisd_level, color = bearCol, width = 1, style = line.style_solid)

// --- Bar Coloring ---
var int sfp_trend_state = 0

if bullsfp
    sfp_trend_state := 1
else if bearsfp
    sfp_trend_state := -1
else
    if (sfp_trend_state == 1 and trend == -1)
        sfp_trend_state := 0
    else if (sfp_trend_state == -1 and trend == 1)
        sfp_trend_state := 0

barcolor(sfp_trend_state == 1 ? bullCol : sfp_trend_state == -1 ? bearCol : na)

// --- Labels ---
plotshape(bullsfp ? low : na, "Bullish Signal", shape.labelup, location.absolute, bullCol, size = size.small, text = "▲", textcolor = chart.fg_color)
plotshape(bearsfp ? high : na, "Bearish Signal", shape.labeldown, location.absolute, bearCol, size = size.small, text = "▼", textcolor = chart.fg_color)

// --- Alerts ---
alertcondition(bullsfp, title = "Bullish SFP", message = "Bullish Swing Failure Pattern Detected")
alertcondition(bearsfp, title = "Bearish SFP", message = "Bearish Swing Failure Pattern Detected")