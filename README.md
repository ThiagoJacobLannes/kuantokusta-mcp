# kuantokusta-mcp

MCP server for [KuantoKusta.pt](https://www.kuantokusta.pt/) — Portugal's main product price comparator.

## Tools

### `search`
Search for products with optional filters. Returns up to 48 products per page sorted by your chosen order (default: cheapest first).

Parameters:
- `query` — search keywords (e.g. `"frigorifico"`, `"iphone 15"`, `"tv 55"`)
- `area` — category filter (e.g. `"Electrodomésticos"`, `"Informática"`, `"Televisores"`)
- `sort` — `"cheap"` (default) | `"expensive"` | `"popular"` | `"alphabetical"` | `"newest"`
- `price_min` / `price_max` — price range in EUR (integers)
- `brands` — list of brand names (e.g. `["Samsung", "LG"]`)
- `page` — page number (default 1)

### `get_product`
Get full details for a product URL from a `search` result: name, description, 45+ tech specs, and all store offers sorted by total price (product + shipping).

## Install

```bash
cd tools/kuantokusta-mcp
python -m pip install -e .
```

## Add to Claude Desktop

Merge `claude_desktop_config.snippet.json` into your Claude Desktop `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "kuantokusta": {
      "command": "python",
      "args": ["-m", "kuantokusta_mcp.server"]
    }
  }
}
```
