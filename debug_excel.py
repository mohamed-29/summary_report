import pandas as pd
import sys
import os

def inspect_excel(file_path):
    print(f"--- Inspecting {file_path} ---")
    
    if not os.path.exists(file_path):
        print("File not found.")
        return

    try:
        df = pd.read_excel(file_path, engine='openpyxl')
    except Exception as e:
        print(f"Error reading Excel: {e}")
        return

    print(f"Total Rows: {len(df)}")
    print("\nColumns found:")
    for col in df.columns:
        print(f" - '{col}' (Type: {df[col].dtype})")

    # possible void columns
    void_keywords = ['void', 'voids', 'الفويد', 'عدد ال']
    potential_void_cols = [c for c in df.columns if any(k in str(c).lower() for k in void_keywords)]

    print(f"\nPotential 'Voids' columns found: {potential_void_cols}")

    for col in potential_void_cols:
        print(f"\nAnalyzing column: '{col}'")
        non_null = df[col].count()
        print(f" Non-null values: {non_null}")
        
        # Try to sum
        total = 0
        numeric_count = 0
        non_numeric_values = []
        
        for val in df[col]:
            try:
                # cleaner logic
                val_str = str(val).strip()
                if val_str in ['-', '/', '', 'nan', 'None']:
                    continue
                num = float(val_str)
                total += num
                numeric_count += 1
            except:
                non_numeric_values.append(val)
        
        print(f" calculated_sum: {total}")
        print(f" parsed_numeric_rows: {numeric_count}")
        if non_numeric_values:
            print(f" Sample non-numeric values: {non_numeric_values[:5]}")

if len(sys.argv) > 1:
    inspect_excel(sys.argv[1])
else:
    print("Please provide a file path.")
