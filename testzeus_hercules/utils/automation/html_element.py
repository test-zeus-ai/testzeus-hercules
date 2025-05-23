def generate_xpath(attributes):
    """
    Generate an XPath expression using the most reliable attributes first.
    
    Args:
        attributes (dict): Dictionary of element attributes including tagName
        
    Returns:
        str: XPath expression that matches the element based on key attributes
    """
    # Define priority order of attributes to use for XPath generation
    PRIORITY_ATTRIBUTES = [
        'id',                        # IDs are unique and most reliable
        'name',                      # Names are often unique
        'data-testid', 'data-test',  # Test attributes specifically for testing
        'data-qa', 'data-cy',        # Common test automation attributes
        'aria-label',                # Accessibility labels
        'title',                     # Title attributes
        'class',                     # Class names (used carefully)
        'href', 'src',               # Link and image sources
        'value',                     # Input values
        'type',                      # Input types
        'alt',                       # Image alt text
        'role',                      # ARIA roles
    ]
    
    tag_name = attributes.get('tagName', '*')
    xpath_parts = [f"//{tag_name}"]
    conditions = []
    
    # Check priority attributes in order
    for attr in PRIORITY_ATTRIBUTES:
        if attr in attributes and attributes[attr]:
            value = attributes[attr]
            
            # Handle special cases
            if attr == 'class':
                # For class, we need to check contains (since elements can have multiple classes)
                conditions.append(f"contains(@class, '{value}')")
            else:
                # For other attributes, exact match
                value_escaped = str(value).replace("'", "\\'")
                conditions.append(f"@{attr}='{value_escaped}'")
            
    
    # If no priority attributes found, fall back to other non-empty attributes
    if not conditions:
        for attr, value in attributes.items():
            if attr != 'tagName' and value and attr not in PRIORITY_ATTRIBUTES:
                value_escaped = str(value).replace("'", "\\'")
                conditions.append(f"@{attr}='{value_escaped}'")
                break  # Just use one as fallback
    
    if conditions:
        xpath_parts.append(f"[{' and '.join(conditions)}]")
    
    return "".join(xpath_parts)