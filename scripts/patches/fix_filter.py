# Apply fix to backtest.py - add field aliasing for global filters

with open('C:/Axiom/Axiom/strategies/backtest.py', 'r') as f:
    lines = f.readlines()

# Find the line numbers we need to modify
new_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    
    # Insert field_aliases dict after "return pd.Series(True, index=df.index)" 
    # and before "mask = pd.Series(True, index=df.index)"
    if 'return pd.Series(True, index=df.index)' in line and i > 0 and 'if not filters' in lines[i-5]:
        new_lines.append(line)
        new_lines.append('\n')
        # Check if next non-empty line is mask = pd.Series...
        j = i + 1
        while j < len(lines) and lines[j].strip() == '':
            new_lines.append(lines[j])
            j += 1
        if j < len(lines) and 'mask = pd.Series(True, index=df.index)' in lines[j]:
            # Insert field_aliases
            new_lines.append('    # Field alias mapping for common variations\n')
            new_lines.append('    field_aliases = {\n')
            new_lines.append('        "adx": "adx_val",\n')
            new_lines.append('        "rsi": "rsi_val",\n')
            new_lines.append('        "atr": "atr_val",\n')
            new_lines.append('    }\n')
            new_lines.append('\n')
    
    # Fix the field lookup to use aliases
    elif 'if field not in df.columns:' in line:
        # Replace this block
        new_lines.append('        # Resolve alias if needed\n')
        new_lines.append('        resolved_field = field_aliases.get(field, field)\n')
        new_lines.append('        \n')
        new_lines.append('        if resolved_field not in df.columns:\n')
        new_lines.append('            # Try original field name\n')
        new_lines.append('            if field not in df.columns:\n')
        new_lines.append('                log.warning("Global filter field \'%s\' not in dataframe, skipping filter", field)\n')
        new_lines.append('                continue\n')
        new_lines.append('            resolved_field = field\n')
        new_lines.append('        \n')
        new_lines.append('        field = resolved_field\n')
        # Skip the next 2 lines (the old warning and continue)
        i += 2
    else:
        new_lines.append(line)
    
    i += 1

with open('C:/Axiom/Axiom/strategies/backtest.py', 'w') as f:
    f.writelines(new_lines)

print('Fix applied successfully')
# Apply fix to backtest.py - add field aliasing for global filters

with open('C:/Axiom/Axiom/strategies/backtest.py', 'r') as f:
    lines = f.readlines()

# Find the line numbers we need to modify
new_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    
    # Insert field_aliases dict after "return pd.Series(True, index=df.index)" 
    # and before "mask = pd.Series(True, index=df.index)"
    if 'return pd.Series(True, index=df.index)' in line and i > 0 and 'if not filters' in lines[i-5]:
        new_lines.append(line)
        new_lines.append('\n')
        # Check if next non-empty line is mask = pd.Series...
        j = i + 1
        while j < len(lines) and lines[j].strip() == '':
            new_lines.append(lines[j])
            j += 1
        if j < len(lines) and 'mask = pd.Series(True, index=df.index)' in lines[j]:
            # Insert field_aliases
            new_lines.append('    # Field alias mapping for common variations\n')
            new_lines.append('    field_aliases = {\n')
            new_lines.append('        "adx": "adx_val",\n')
            new_lines.append('        "rsi": "rsi_val",\n')
            new_lines.append('        "atr": "atr_val",\n')
            new_lines.append('    }\n')
            new_lines.append('\n')
    
    # Fix the field lookup to use aliases
    elif 'if field not in df.columns:' in line:
        # Replace this block
        new_lines.append('        # Resolve alias if needed\n')
        new_lines.append('        resolved_field = field_aliases.get(field, field)\n')
        new_lines.append('        \n')
        new_lines.append('        if resolved_field not in df.columns:\n')
        new_lines.append('            # Try original field name\n')
        new_lines.append('            if field not in df.columns:\n')
        new_lines.append('                log.warning("Global filter field \'%s\' not in dataframe, skipping filter", field)\n')
        new_lines.append('                continue\n')
        new_lines.append('            resolved_field = field\n')
        new_lines.append('        \n')
        new_lines.append('        field = resolved_field\n')
        # Skip the next 2 lines (the old warning and continue)
        i += 2
    else:
        new_lines.append(line)
    
    i += 1

with open('C:/Axiom/Axiom/strategies/backtest.py', 'w') as f:
    f.writelines(new_lines)

print('Fix applied successfully')
