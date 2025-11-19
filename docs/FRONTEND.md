# Frontend Framework Documentation

## Overview

This project uses a modern, lightweight frontend stack that is fully localized (no CDN dependencies) and optimized for multi-server management.

## Technology Stack

### 1. Jinja2 Templates
**Why Jinja2?**
- Native support in FastAPI via `fastapi.templating`
- Server-side rendering for better performance
- Template inheritance for code reusability
- Simple syntax, easy to learn and maintain
- Perfect for data-driven dashboards

**Usage:**
- `base.html` - Base template with navigation and footer
- `home.html` - Landing page
- `servers.html` - Server list management page
- `server_detail.html` - Individual server management with real-time monitoring

### 2. Bootstrap 5
**Why Bootstrap 5?**
- Industry-standard CSS framework
- Responsive grid system perfect for dashboards
- Comprehensive component library
- Excellent for admin/management interfaces
- Strong community and documentation

**Version:** 5.3.2 (downloaded locally)

**Features Used:**
- Responsive grid system
- Cards for server information
- Modals for forms
- Buttons and badges
- Navigation bar
- Tables for logs and history

### 3. Alpine.js
**Why Alpine.js?**
- Lightweight (only 43KB) - perfect for dashboards
- Reactive data binding without heavy frameworks
- Simple syntax directly in HTML
- No build step required
- Ideal for interactive components without Vue/React overhead

**Version:** 3.13.3 (downloaded locally)

**Usage:**
- Server list state management
- Real-time WebSocket connection handling
- Form handling and validation
- Dynamic UI updates

### 4. Bootstrap Icons
**Why Bootstrap Icons?**
- Comprehensive icon set (1,800+ icons)
- Perfect match with Bootstrap
- SVG-based, scalable
- Fully localized with web fonts

**Version:** 1.11.2 (downloaded locally)

## File Structure

```
static/
├── css/
│   ├── bootstrap.min.css          # Bootstrap CSS framework
│   ├── bootstrap-icons.min.css    # Bootstrap Icons CSS
│   └── app.css                     # Custom application styles
├── js/
│   ├── bootstrap.bundle.min.js    # Bootstrap JS (includes Popper)
│   └── alpine.min.js              # Alpine.js framework
└── fonts/
    ├── bootstrap-icons.woff       # Icon font (WOFF format)
    └── bootstrap-icons.woff2      # Icon font (WOFF2 format)

templates/
├── base.html                      # Base template
├── home.html                      # Home page
├── servers.html                   # Server list page
└── server_detail.html             # Server detail page
```

## Key Features

### 1. Fully Localized Assets
All CSS, JavaScript, and font files are stored locally in the `static/` directory. This ensures:
- No external dependencies
- Faster loading times
- Works in offline/air-gapped environments
- No privacy concerns from CDN tracking
- Consistent versions across deployments

### 2. Responsive Design
The interface adapts to different screen sizes:
- Desktop: Full multi-column layout
- Tablet: Optimized column widths
- Mobile: Single column, touch-friendly buttons

### 3. Real-time Updates
Uses WebSocket connections with Alpine.js for:
- Live deployment logs
- Server status updates
- Action feedback
- Connection status indicators

### 4. Component-Based Architecture
Templates use Jinja2 inheritance:
```html
{% extends "base.html" %}
{% block content %}
  <!-- Page-specific content -->
{% endblock %}
```

### 5. Custom Theming
`app.css` provides:
- Custom color scheme (purple/blue gradient)
- Server status badges with animated indicators
- Terminal-style log display
- Card hover effects
- Responsive utilities

## Pages Overview

### Home Page (`/`)
- Overview of features
- Quick navigation
- Information cards
- Getting started guide

### Servers List (`/servers-ui`)
- Grid of server cards
- Real-time status indicators
- Add server modal form
- Quick actions

### Server Detail (`/servers-ui/{id}`)
- Detailed server information
- Action buttons (deploy, start, stop, restart, status)
- Live deployment logs via WebSocket
- Deployment history table

## Development Guidelines

### Adding New Pages

1. Create a new Jinja2 template in `templates/`:
```html
{% extends "base.html" %}
{% block title %}Page Title{% endblock %}
{% block content %}
  <!-- Your content -->
{% endblock %}
```

2. Add route in `main.py`:
```python
@app.get("/my-page", response_class=HTMLResponse)
async def my_page(request: Request):
    return templates.TemplateResponse("my_page.html", {"request": request})
```

### Using Alpine.js

Add reactive components using `x-data`:
```html
<div x-data="{ count: 0 }">
    <button @click="count++">Increment</button>
    <span x-text="count"></span>
</div>
```

### Custom Styling

Add custom CSS to `static/css/app.css`:
```css
.my-custom-class {
    /* Your styles */
}
```

## Performance Considerations

1. **Minified Assets**: All CSS and JS files are minified
2. **Local Hosting**: No external requests, faster load times
3. **Async Loading**: JavaScript loaded with `defer` attribute
4. **Caching**: Static files can be cached by browsers

## Browser Compatibility

- Chrome/Edge: Full support
- Firefox: Full support
- Safari: Full support
- Mobile browsers: Full support

Minimum versions:
- Chrome 90+
- Firefox 88+
- Safari 14+
- Edge 90+

## Accessibility

- Semantic HTML structure
- ARIA labels where appropriate
- Keyboard navigation support
- Responsive text sizing
- High contrast colors

## Security

- No external CDN dependencies (no tracking)
- Content Security Policy friendly
- XSS protection via Jinja2 auto-escaping
- CORS not required for static assets

## Future Enhancements

Potential improvements:
1. Add dark mode toggle
2. Add more chart visualizations
3. Add server grouping/filtering
4. Add export functionality
5. Add notification system
6. Add user authentication UI

## Maintenance

### Updating Dependencies

To update frontend libraries:

1. Download new versions from official sources
2. Replace files in `static/` directory
3. Update version numbers in this documentation
4. Test thoroughly

### Adding Icons

Bootstrap Icons are already included. To use:
```html
<i class="bi bi-icon-name"></i>
```

Browse all icons at: https://icons.getbootstrap.com/

## Conclusion

This frontend stack provides:
- ✅ Modern, responsive UI
- ✅ Lightweight and fast
- ✅ Fully localized (no CDNs)
- ✅ Easy to maintain
- ✅ Perfect for server management dashboards
- ✅ Real-time capabilities
- ✅ Mobile-friendly
