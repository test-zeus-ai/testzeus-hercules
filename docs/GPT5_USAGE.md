# GPT-5 Support in Hercules

This document explains how to use GPT-5 models (gpt-5, gpt-5-mini, gpt-5-nano) with the Hercules framework.

## Overview

GPT-5 models use `max_completion_tokens` instead of `max_tokens` for controlling response length. The Hercules framework automatically handles this parameter conversion based on the model family.

## Supported GPT-5 Models

- `gpt-5` - Full GPT-5 model
- `gpt-5-mini` - Smaller, faster GPT-5 variant
- `gpt-5-nano` - Lightweight GPT-5 variant
- `gpt-5-2025-08-07` - Specific GPT-5 version
- `gpt-5-mini-2025-08-07` - Specific GPT-5-mini version
- `gpt-5-nano-2025-08-07` - Specific GPT-5-nano version

## Configuration Methods

### 1. Configuration File

Use `agents_llm_config.json` when you want different GPT-5-family models for
planner, navigation, memory, and helper roles:

```json
{
  "openai_gpt5": {
    "planner_agent": {
      "model_name": "gpt-5",
      "model_api_key": "your-api-key",
      "model_api_type": "openai",
      "llm_config_params": {
        "temperature": 0.0,
        "max_completion_tokens": 4096,
        "seed": 12345
      }
    },
    "nav_agent": {
      "model_name": "gpt-5-mini",
      "model_api_key": "your-api-key",
      "model_api_type": "openai",
      "llm_config_params": {
        "temperature": 0.0,
        "max_completion_tokens": 2048
      }
    },
    "mem_agent": {
      "model_name": "gpt-5-nano",
      "model_api_key": "your-api-key",
      "model_api_type": "openai",
      "llm_config_params": {
        "temperature": 0.0,
        "max_completion_tokens": 1024
      }
    },
    "helper_agent": {
      "model_name": "gpt-5",
      "model_api_key": "your-api-key",
      "model_api_type": "openai",
      "llm_config_params": {
        "temperature": 0.0,
        "max_completion_tokens": 4096
      }
    }
  }
}
```

Then set the environment variable to use this configuration:

```bash
export AGENTS_LLM_CONFIG_FILE=./agents_llm_config.json
export AGENTS_LLM_CONFIG_FILE_REF_KEY=openai_gpt5
```

### 2. Direct Environment Variables

Direct `LLM_MODEL_*` values are still supported for simple single-model runs:

```bash
export LLM_MODEL_NAME=gpt-5
export LLM_MODEL_API_KEY=your-api-key
export LLM_MODEL_API_TYPE=openai
export LLM_MODEL_TEMPERATURE=0.0
```

For GPT-5 response limits, prefer `max_completion_tokens` inside
`agents_llm_config.json`. The config adapter can translate `max_tokens` to
`max_completion_tokens` for GPT-5 model names.

## Automatic Parameter Conversion

The framework automatically converts parameters based on the model family:

- **GPT-5 models**: `max_tokens` → `max_completion_tokens`
- **Claude models**: `max_tokens` → `max_tokens_to_sample`
- **Other models**: `max_completion_tokens` → `max_tokens` (if needed)

### Example Conversions

```python
# Input config for GPT-5
config = {
    "max_tokens": 2048,  # This will be converted
    "temperature": 0.1
}

# After adaptation for gpt-5 model
result = {
    "max_completion_tokens": 2048,  # Converted from max_tokens
    "temperature": 0.1,
    "model": "gpt-5"
}
```

## Testing

Run the model utility and LLM helper tests that cover parameter adaptation:

```bash
uv run pytest tests/test_llm_helper.py -q
```

## Troubleshooting

### Common Issues

1. **Parameter not recognized**: Ensure you're using `max_completion_tokens` for GPT-5 models
2. **Conversion errors**: Check that the model name starts with the correct prefix
3. **API errors**: Verify your OpenAI API key has access to GPT-5 models

### Debug Mode

Enable debug logging to see parameter conversions:

```bash
export LOG_LEVEL=DEBUG
```

## Migration from GPT-4

If you're migrating from GPT-4 to GPT-5:

1. Update model names in your configuration
2. Replace `max_tokens` with `max_completion_tokens`
3. Test with smaller token limits first
4. Verify your OpenAI API key has access to GPT-5 models



## Support

For issues with GPT-5 support:

1. Check the logs for parameter conversion messages
2. Verify your configuration format
3. Run `uv run pytest tests/test_llm_helper.py -q`
4. Review the main documentation for additional details
