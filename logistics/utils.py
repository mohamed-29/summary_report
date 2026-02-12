"""
Utility functions for the logistics app.
Includes intelligent entity resolution for machine name matching.
"""

import logging
from typing import Optional
from rapidfuzz import fuzz, process
from django.conf import settings
from .models import Machine, MachineAlias

logger = logging.getLogger(__name__)

# Threshold for fuzzy matching confidence
FUZZY_MATCH_THRESHOLD = 85


def get_gemini_model():
    """Initialize and return a Gemini model instance."""
    try:
        import google.generativeai as genai
        api_key = getattr(settings, 'GEMINI_API_KEY', '')
        if not api_key:
            logger.warning("GEMINI_API_KEY not configured. AI fallback disabled.")
            return None
        genai.configure(api_key=api_key)
        return genai.GenerativeModel('gemini-3-flash-preview')
    except ImportError:
        logger.error("google-generativeai package not installed.")
        return None
    except Exception as e:
        logger.error(f"Failed to initialize Gemini model: {e}")
        return None


def resolve_machine(input_name: str, use_ai_fallback: bool = True) -> Optional[Machine]:
    """
    Resolve a raw machine name to a canonical Machine object.
    
    Uses a multi-step approach:
    1. Exact match in Machine table
    2. Alias lookup in MachineAlias table
    3. Fuzzy matching using RapidFuzz
    4. AI fallback using Gemini (optional)
    
    Args:
        input_name: The raw machine name from Excel/input
        use_ai_fallback: Whether to use Gemini AI if other methods fail
        
    Returns:
        Machine object if resolved, None otherwise
    """
    if not input_name or not input_name.strip():
        logger.warning("Empty machine name provided")
        return None
    
    # Normalize the input
    clean_name = input_name.strip()
    clean_name_lower = clean_name.lower()
    
    # Step 1: Exact match
    try:
        machine = Machine.objects.get(name__iexact=clean_name)
        logger.debug(f"Exact match found: '{clean_name}' -> '{machine.name}'")
        return machine
    except Machine.DoesNotExist:
        pass
    
    # Step 2: Alias lookup
    try:
        alias_entry = MachineAlias.objects.select_related('machine').get(alias__iexact=clean_name)
        logger.debug(f"Alias match found: '{clean_name}' -> '{alias_entry.machine.name}'")
        return alias_entry.machine
    except MachineAlias.DoesNotExist:
        pass
    
    # Step 3: Fuzzy matching
    valid_machines = list(Machine.objects.values_list('name', flat=True))
    
    if valid_machines:
        result = process.extractOne(
            clean_name,
            valid_machines,
            scorer=fuzz.WRatio
        )
        
        if result:
            best_match, score, _ = result
            if score >= FUZZY_MATCH_THRESHOLD:
                machine = Machine.objects.get(name=best_match)
                # Save this mapping for future speed
                MachineAlias.objects.create(
                    alias=clean_name,
                    machine=machine,
                    source='fuzzy',
                    confidence_score=score / 100.0
                )
                logger.info(f"Fuzzy match ({score}%): '{clean_name}' -> '{machine.name}'")
                return machine
    
    # Step 4: AI Fallback (Gemini)
    if use_ai_fallback and valid_machines:
        machine = _ai_resolve_machine(clean_name, valid_machines)
        if machine:
            return machine
    
    # Step 5: No match found
    logger.warning(f"Could not resolve machine: '{input_name}'. Needs manual review.")
    return None


def _ai_resolve_machine(input_name: str, valid_machines: list) -> Optional[Machine]:
    """
    Use Gemini AI to semantically match a machine name.
    
    Args:
        input_name: The raw machine name
        valid_machines: List of valid canonical machine names
        
    Returns:
        Machine object if AI finds a match, None otherwise
    """
    model = get_gemini_model()
    if not model:
        return None
    
    try:
        # Build prompt
        machines_list = "\n".join([f"- {m}" for m in valid_machines])
        prompt = f"""You are a data cleaning assistant. I have a list of valid vending machine names:

{machines_list}

The user input is: "{input_name}"

Which valid machine name does this most likely refer to? Consider typos, abbreviations, translations, and semantic similarity.

If you find a match, respond with ONLY the exact valid machine name from the list above.
If none match or you're unsure, respond with exactly: None

Response (exact match string or "None"):"""

        response = model.generate_content(prompt)
        ai_suggestion = response.text.strip()
        
        # Validate AI response
        if ai_suggestion and ai_suggestion != "None" and ai_suggestion in valid_machines:
            machine = Machine.objects.get(name=ai_suggestion)
            # Save this mapping
            MachineAlias.objects.create(
                alias=input_name,
                machine=machine,
                source='ai',
                confidence_score=0.8  # AI matches get a default confidence
            )
            logger.info(f"AI match: '{input_name}' -> '{machine.name}'")
            return machine
        else:
            logger.debug(f"AI could not match: '{input_name}' (response: {ai_suggestion})")
            
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
    
    return None


def clean_numeric_value(value, default=0):
    """
    Clean numeric values from Excel.
    Handles '/' placeholders, empty strings, and other non-numeric values.
    
    Args:
        value: The raw value from Excel
        default: Default value if cleaning fails
        
    Returns:
        Integer value
    """
    if value is None:
        return default
    
    # Convert to string for processing
    str_value = str(value).strip()
    
    # Handle common placeholders
    if str_value in ['/', '-', '', 'nan', 'None', 'N/A', 'n/a']:
        return default
    
    try:
        # Try to parse as float first, then convert to int
        return int(float(str_value))
    except (ValueError, TypeError):
        return default


def get_or_create_operator(name: str, is_driver: bool = False):
    """
    Get or create an Operator by name.
    
    Args:
        name: Operator name
        is_driver: Whether this operator is a driver
        
    Returns:
        Tuple of (Operator, created)
    """
    if not name or not name.strip():
        return None, False
    
    clean_name = name.strip()
    operator, created = Operator.objects.get_or_create(
        name__iexact=clean_name,
        defaults={'name': clean_name, 'is_driver': is_driver}
    )
    
    # Update is_driver if needed
    if not created and is_driver and not operator.is_driver:
        operator.is_driver = True
        operator.save(update_fields=['is_driver'])
    
    return operator, created


# Import Operator here to avoid circular imports
from .models import Operator


def find_best_column(columns, candidates, keywords=None):
    """
    Find the best matching column from a list of available columns.
    
    Priority:
    1. Exact match in candidates list.
    2. Case-insensitive match in candidates list.
    3. Partial match with keywords (if provided).
    
    Args:
        columns: List of available column names from the dataframe.
        candidates: List of expected column names (exact matches).
        keywords: List of substrings to search for if exact match fails.
        
    Returns:
        The matching column name or None.
    """
    # 1. Exact match
    for candidate in candidates:
        if candidate in columns:
            return candidate
            
    # 2. Case-insensitive match
    columns_lower = {str(c).lower(): c for c in columns}
    for candidate in candidates:
        if candidate.lower() in columns_lower:
            return columns_lower[candidate.lower()]
            
    # 3. Keyword match
    if keywords:
        for col in columns:
            col_str = str(col).lower()
            if any(k.lower() in col_str for k in keywords):
                return col
                
    return None


def batch_ai_resolve_machines(raw_names: list) -> dict:
    """
    Resolve a list of raw machine names using Gemini AI in batches.
    
    Args:
        raw_names: List of unresolved raw machine names.
        
    Returns:
        Dictionary mapping {raw_name: resolved_machine_object or None}
    """
    import json
    import time
    from .models import Machine, MachineAlias

    valid_machines = list(Machine.objects.values_list('name', flat=True))
    if not valid_machines:
        return {}
    
    machines_list_str = "\n".join([f"- {m}" for m in valid_machines])
    results = {}
    
    # Process in batches of 20
    BATCH_SIZE = 20
    model = get_gemini_model()
    
    if not model:
        return {}

    for i in range(0, len(raw_names), BATCH_SIZE):
        batch = raw_names[i:i+BATCH_SIZE]
        
        prompt = f"""You are a data cleaning assistant. I have a list of valid vending machine names:

{machines_list_str}

I have a list of raw names from a file that need to be matched to the valid names above.
Match them based on typos, abbreviations, translations (Arabic/English), or semantic similarity.

Raw Names to Resolve:
{json.dumps(batch, ensure_ascii=False)}

Return a JSON object where keys are the "Raw Names" and values are the EXACT "Valid Machine Name" from the list.
If a raw name does not match any valid machine, set the value to null.

Example Output:
{{"raw name 1": "Valid Name A", "raw name 2": null}}

Response (JSON only):"""

        try:
            response = model.generate_content(prompt)
            text = response.text.strip()
            if text.startswith('```json'): # Clean code blocks
                text = text[7:-3]
            elif text.startswith('```'):
                text = text[3:-3]
                
            batch_results = json.loads(text)
            
            # Process results and save aliases
            for raw, resolved_name in batch_results.items():
                if resolved_name and resolved_name in valid_machines:
                    try:
                        machine = Machine.objects.get(name=resolved_name)
                        results[raw] = machine
                        
                        # Save alias for future speed
                        MachineAlias.objects.get_or_create(
                            alias=raw,
                            defaults={
                                'machine': machine,
                                'source': 'ai_batch',
                                'confidence_score': 0.85
                            }
                        )
                    except Machine.DoesNotExist:
                        results[raw] = None
                else:
                    results[raw] = None
            
            time.sleep(1) # Rate limit
            
        except Exception as e:
            logger.error(f"Batch AI resolution failed: {e}")
            for name in batch:
                results[name] = None
                
    return results
