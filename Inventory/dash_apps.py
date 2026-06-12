import dash
from dash import dcc, html, Input, Output, callback, ctx, State
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
from django_plotly_dash import DjangoDash
from .models import Equipment, Department, Workshop
from django.contrib.auth.decorators import login_required
from django_plotly_dash.access import login_required as dash_login_required
from django.db.models import Count
import json

# Initialize the Dash app
app = DjangoDash('InventoryDashboard')

# Enhanced layout with full-screen optimization
app.layout = html.Div(
    style={
        'margin': '0',
        'padding': '0',
        'fontFamily': 'Segoe UI, Tahoma, Geneva, Verdana, sans-serif',
        'height': '100%',
        'overflow': 'auto',
        'background': 'transparent'
    },
    children=[
        # Hidden div to store session data
        html.Div(id='session-id', style={'display': 'none'}),

        # Main container with fluid layout
        html.Div(
            className="container-fluid p-4",
            style={
                'height': '700px',
                'width': '100%',
                'padding': '20px',
                'margin': '0',
                'overflow': 'auto',
                'background': 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)'
            },
            children=[
                # Header Section
                html.Div([
                    html.H2(
                        "🔧 Equipment Analytics Dashboard",
                        className="text-center mb-0",
                        style={
                            'color': '#fff',
                            'fontWeight': 'bold',
                            'textShadow': '1px 1px 3px rgba(0,0,0,0.2)',
                            'background': "linear-gradient(90deg, #6a11cb 0%, #2575fc 100%)",
                            'padding': '20px',
                            'borderRadius': '12px',
                            'marginBottom': '20px'
                        }
                    ),
                    html.P(
                        "Interactive insights for equipment management",
                        className="text-center text-light",
                        style={'fontSize': '16px', 'marginTop': '10px'}
                    )
                ], className="mb-4"),

                # Control Panel
                html.Div(className="row", children=[
                    html.Div([
                        html.Label("📊 Chart Type", className="form-label fw-bold", style={'color': 'white'}),
                        dcc.Dropdown(
                            id='chart-type-dropdown',
                            options=[
                                {'label': '📊 Bar Chart', 'value': 'bar'},
                                {'label': '🥧 Pie Chart', 'value': 'pie'},
                                {'label': '🍩 Donut Chart', 'value': 'donut'},
                                {'label': '📈 Horizontal Bar', 'value': 'hbar'},
                                {'label': '🌟 Sunburst', 'value': 'sunburst'},
                                {'label': '📉 Treemap', 'value': 'treemap'}
                            ],
                            value='bar',
                            className="form-select",
                            style={'marginBottom': '10px'}
                        )
                    ], className="col-lg-3 col-md-6 mb-3"),

                    html.Div([
                        html.Label("🏢 Department Filter", className="form-label fw-bold", style={'color': 'white'}),
                        dcc.Dropdown(
                            id='department-dropdown',
                            placeholder="All Departments",
                            className="form-select",
                            style={'marginBottom': '10px'}
                        )
                    ], className="col-lg-3 col-md-6 mb-3"),

                    html.Div([
                        html.Label("🎨 Color Theme", className="form-label fw-bold", style={'color': 'white'}),
                        dcc.Dropdown(
                            id='color-scheme-dropdown',
                            options=[
                                {'label': '🎯 Default Status Colors', 'value': 'default'},
                                {'label': '🌈 Viridis', 'value': 'viridis'},
                                {'label': '🔥 Plasma', 'value': 'plasma'},
                                {'label': '🌅 Sunset', 'value': 'sunset'},
                                {'label': '🌊 Ocean', 'value': 'blues'},
                                {'label': '🍃 Nature', 'value': 'greens'}
                            ],
                            value='default',
                            className="form-select",
                            style={'marginBottom': '10px'}
                        )
                    ], className="col-lg-3 col-md-6 mb-3"),

                    html.Div([
                        html.Label("🔄 Auto Refresh", className="form-label fw-bold", style={'color': 'white'}),
                        dcc.Dropdown(
                            id='refresh-interval',
                            options=[
                                {'label': 'Off', 'value': 0},
                                {'label': '30 seconds', 'value': 30000},
                                {'label': '1 minute', 'value': 60000},
                                {'label': '5 minutes', 'value': 300000}
                            ],
                            value=0,
                            className="form-select",
                            style={'marginBottom': '10px'}
                        )
                    ], className="col-lg-3 col-md-6 mb-3"),
                ], style={'marginBottom': '20px'}),

                # Statistics Cards
                html.Div(className="row", children=[
                    html.Div([
                        html.Div([
                            html.Div([
                                html.I(className="fas fa-check-circle fa-2x text-success mb-2"),
                                html.H4(id='working-count', children='0', className="mb-1 text-success fw-bold"),
                                html.P('Working Equipment', className="mb-0 text-muted small")
                            ], className="text-center")
                        ], className="card-body", style={'padding': '20px', 'background': 'white', 'borderRadius': '10px'})
                    ], className="col-xl-3 col-lg-6 col-md-6 col-sm-12 mb-3"),

                    html.Div([
                        html.Div([
                            html.Div([
                                html.I(className="fas fa-times-circle fa-2x text-danger mb-2"),
                                html.H4(id='not-working-count', children='0', className="mb-1 text-danger fw-bold"),
                                html.P('Not Working', className="mb-0 text-muted small")
                            ], className="text-center")
                        ], className="card-body", style={'padding': '20px', 'background': 'white', 'borderRadius': '10px'})
                    ], className="col-xl-3 col-lg-6 col-md-6 col-sm-12 mb-3"),

                    html.Div([
                        html.Div([
                            html.Div([
                                html.I(className="fas fa-wrench fa-2x text-warning mb-2"),
                                html.H4(id='under-repair-count', children='0', className="mb-1 text-warning fw-bold"),
                                html.P('Under Repair', className="mb-0 text-muted small")
                            ], className="text-center")
                        ], className="card-body", style={'padding': '20px', 'background': 'white', 'borderRadius': '10px'})
                    ], className="col-xl-3 col-lg-6 col-md-6 col-sm-12 mb-3"),

                    html.Div([
                        html.Div([
                            html.Div([
                                html.I(className="fas fa-cogs fa-2x text-info mb-2"),
                                html.H4(id='total-count', children='0', className="mb-1 text-info fw-bold"),
                                html.P('Total Equipment', className="mb-0 text-muted small")
                            ], className="text-center")
                        ], className="card-body", style={'padding': '20px', 'background': 'white', 'borderRadius': '10px'})
                    ], className="col-xl-3 col-lg-6 col-md-6 col-sm-12 mb-3"),
                ], style={'marginBottom': '30px'}),

                # Main Chart - LARGER SIZE
                html.Div([
                    html.Div([
                        html.Div([
                            html.H5("📊 Equipment Status Overview", className="card-title mb-3 text-center"),
                            dcc.Graph(
                                id='status-chart',
                                style={'height': '100%', 'width': '30%'},  # FIXED: Set explicit height
                                config={'displayModeBar': True, 'displaylogo': False}
                            )
                        ], className="card-body", style={'background': 'white', 'borderRadius': '15px', 'boxShadow': '0 4px 6px rgba(0, 0, 0, 0.1)'})
                    ], className="card shadow-sm w-100", style={'border': 'none'})
                ], className="mb-4"),

                # Secondary Charts - LARGER SIZE
                html.Div(className="row", children=[
                    html.Div([
                        html.Div([
                            html.Div([
                                html.H5("🏢 Department Analysis", className="card-title mb-3 text-center"),
                                dcc.Graph(
                                    id='department-chart',
                                    style={'height': '400px', 'width': '100%'},  # FIXED: Set explicit height
                                    config={'displayModeBar': True, 'displaylogo': False}
                                )
                            ], className="card-body", style={'background': 'white', 'borderRadius': '15px'})
                        ], className="card shadow-sm h-100", style={'border': 'none'})
                    ], className="col-lg-6 col-12 mb-4"),

                    html.Div([
                        html.Div([
                            html.Div([
                                html.H5("⚙️ Equipment Types", className="card-title mb-3 text-center"),
                                dcc.Graph(
                                    id='equipment-types-chart',
                                    style={'height': '400px', 'width': '100%'},  # FIXED: Set explicit height
                                    config={'displayModeBar': True, 'displaylogo': False}
                                )
                            ], className="card-body", style={'background': 'white', 'borderRadius': '15px'})
                        ], className="card shadow-sm h-100", style={'border': 'none'})
                    ], className="col-lg-6 col-12 mb-4"),
                ]),

                 # Interval for refresh
                 dcc.Interval(
                     id='interval-component',
                     interval=60000,
                     n_intervals=0,
                     disabled=True
                 ),
                 
                 # Theme sync store
                 dcc.Store(id='theme-data', storage_type='session'),
                 dcc.Interval(
                     id='theme-poll',
                     interval=2000,
                     n_intervals=0,
                     disabled=False
                 )
             ]
         )
     ]
 )


# Data fetching functions
def get_equipment_data(workshop=None, department=None):
    """Get equipment data based on user's workshop and optional department filter"""
    try:
        queryset = Equipment.objects.select_related('department__workshop', 'description')
        
        if workshop:
            queryset = queryset.filter(department__workshop=workshop)
        
        if department:
            queryset = queryset.filter(department_id=department)
        
        # Convert to DataFrame
        data = []
        for eq in queryset:
            data.append({
                'status': eq.status,
                'department': eq.department.name,
                'description': eq.description.name,
                'workshop': eq.department.workshop.name if eq.department.workshop else 'Unknown',
                'manufacturer': eq.manufacturer or 'Unknown',
                'model': eq.model or 'Unknown'
            })
        
        return pd.DataFrame(data) if data else pd.DataFrame(columns=[
            'status', 'department', 'description', 'workshop', 'manufacturer', 'model'
        ])
    except Exception as e:
        print(f"Error fetching equipment data: {e}")
        return pd.DataFrame(columns=['status', 'department', 'description', 'workshop', 'manufacturer', 'model'])

def get_departments_for_workshop(workshop=None):
    """Get departments for the user's workshop"""
    try:
        if workshop:
            departments = Department.objects.filter(workshop=workshop).values_list('id', 'name')
            return [{'label': f"🏢 {name}", 'value': dept_id} for dept_id, name in departments]
        return []
    except Exception as e:
        print(f"Error fetching departments: {e}")
        return []

def get_color_scheme(color_scheme, data_type='status'):
    """Get color scheme based on selection and data type"""
    if color_scheme == 'default' and data_type == 'status':
        return {
            'Working': '#28a745',      # Green
            'Not working': '#dc3545',  # Red
            'Under repair': '#ffc107'  # Yellow/Orange
        }
    
    color_schemes = {
        'viridis': px.colors.sequential.Viridis,
        'plasma': px.colors.sequential.Plasma,
        'sunset': px.colors.sequential.Sunset,
        'blues': px.colors.sequential.Blues,
        'greens': px.colors.sequential.Greens
    }
    
    return color_schemes.get(color_scheme, px.colors.qualitative.Set3)

def create_empty_figure(message="No data available"):
    """Create an empty figure with a message"""
    fig = go.Figure()
    fig.add_annotation(
        x=0.5, y=0.5,
        text=message,
        showarrow=False,
        font=dict(size=16, color="gray"),
        xref="paper", yref="paper"
    )
    fig.update_layout(
        showlegend=False,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        plot_bgcolor='white',
        paper_bgcolor='white',
        height=450,  # FIXED: Set proper height
        margin=dict(l=40, r=40, t=60, b=40)
    )
    return fig

def create_status_chart(df, chart_type, color_scheme):
    """Create the main status chart based on type"""
    if df.empty:
        return create_empty_figure("No equipment data available")
    
    # Count by status
    status_counts = df['status'].value_counts()
    
    if status_counts.empty:
        return create_empty_figure("No status data available")
    
    colors = get_color_scheme(color_scheme, 'status')
    
    if chart_type == 'bar':
        fig = px.bar(
            x=status_counts.index, 
            y=status_counts.values,
            labels={'x': 'Status', 'y': 'Count'},
            title="Equipment Status Distribution",
            color=status_counts.index,
            color_discrete_map=colors if isinstance(colors, dict) else None,
            color_discrete_sequence=colors if isinstance(colors, list) else None
        )
        fig.update_layout(showlegend=False)
        
    elif chart_type == 'hbar':
        fig = px.bar(
            y=status_counts.index, 
            x=status_counts.values,
            orientation='h',
            labels={'x': 'Count', 'y': 'Status'},
            title="Equipment Status Distribution",
            color=status_counts.index,
            color_discrete_map=colors if isinstance(colors, dict) else None,
            color_discrete_sequence=colors if isinstance(colors, list) else None
        )
        fig.update_layout(showlegend=False)
        
    elif chart_type == 'pie':
        fig = px.pie(
            values=status_counts.values, 
            names=status_counts.index,
            title="Equipment Status Distribution",
            color=status_counts.index,
            color_discrete_map=colors if isinstance(colors, dict) else None,
            color_discrete_sequence=colors if isinstance(colors, list) else None
        )
        
    elif chart_type == 'donut':
        fig = px.pie(
            values=status_counts.values, 
            names=status_counts.index,
            title="Equipment Status Distribution",
            hole=0.4,
            color=status_counts.index,
            color_discrete_map=colors if isinstance(colors, dict) else None,
            color_discrete_sequence=colors if isinstance(colors, list) else None
        )
        
    elif chart_type == 'sunburst':
        # Create hierarchical data for sunburst
        sunburst_data = []
        for status in status_counts.index:
            dept_data = df[df['status'] == status]['department'].value_counts()
            for dept, count in dept_data.items():
                sunburst_data.append({
                    'ids': f"{status}-{dept}",
                    'labels': dept,
                    'parents': status,
                    'values': count
                })
        
        # Add root level
        for status in status_counts.index:
            sunburst_data.append({
                'ids': status,
                'labels': status,
                'parents': '',
                'values': status_counts[status]
            })
        
        sunburst_df = pd.DataFrame(sunburst_data)
        fig = px.sunburst(
            sunburst_df,
            ids='ids',
            names='labels',
            parents='parents',
            values='values',
            title="Equipment Status & Department Distribution"
        )
        
    elif chart_type == 'treemap':
        # Create treemap data
        treemap_data = []
        for status in status_counts.index:
            dept_data = df[df['status'] == status]['department'].value_counts()
            for dept, count in dept_data.items():
                treemap_data.append({
                    'status': status,
                    'department': dept,
                    'count': count
                })
        
        treemap_df = pd.DataFrame(treemap_data)
        if not treemap_df.empty:
            fig = px.treemap(
                treemap_df,
                path=[px.Constant("All Equipment"), 'status', 'department'],
                values='count',
                title="Equipment Status & Department Treemap",
                color='status',
                color_discrete_map=colors if isinstance(colors, dict) else None
            )
        else:
            fig = create_empty_figure("No data for treemap")
    
    else:
        fig = create_empty_figure("Invalid chart type")
    
    # FIXED: Update layout for all chart types with proper height and styling
    fig.update_layout(
        plot_bgcolor='white',
        paper_bgcolor='white',
        font=dict(size=14),
        title_font=dict(size=18, color='#2c3e50'),
        margin=dict(l=60, r=60, t=80, b=60),  # FIXED: Better margins
        height=450,  # FIXED: Set consistent height for main chart
        autosize=True
    )
    
    return fig

def create_department_chart(df, color_scheme):
    """Create department distribution chart"""
    if df.empty:
        return create_empty_figure("No department data available")
    
    dept_counts = df['department'].value_counts()
    colors = get_color_scheme(color_scheme, 'department')
    
    fig = px.bar(
        x=dept_counts.values,
        y=dept_counts.index,
        orientation='h',
        labels={'x': 'Equipment Count', 'y': 'Department'},
        title="Equipment by Department",
        color_discrete_sequence=colors if isinstance(colors, list) else None
    )
    
    fig.update_layout(
        plot_bgcolor='white',
        paper_bgcolor='white',
        font=dict(size=12),
        title_font=dict(size=16, color='#2c3e50'),
        margin=dict(l=80, r=40, t=60, b=40),  # FIXED: Better left margin for department names
        showlegend=False,
        height=350,  # FIXED: Set explicit height
        autosize=True
    )
    
    return fig

def create_equipment_types_chart(df, color_scheme):
    """Create equipment types distribution chart"""
    if df.empty:
        return create_empty_figure("No equipment types data available")
    
    type_counts = df['description'].value_counts().head(10)  # Top 10 equipment types
    colors = get_color_scheme(color_scheme, 'types')
    
    fig = px.pie(
        values=type_counts.values,
        names=type_counts.index,
        title="Top Equipment Types",
        color_discrete_sequence=colors if isinstance(colors, list) else None
    )
    
    fig.update_traces(textposition='inside', textinfo='percent+label')
    fig.update_layout(
        plot_bgcolor='white',
        paper_bgcolor='white',
        font=dict(size=12),
        title_font=dict(size=16, color='#2c3e50'),
        margin=dict(l=40, r=40, t=60, b=40),
        height=950,  # FIXED: Set explicit height
        autosize=True
    )
    
    return fig

# Main callback for dashboard updates
@app.callback(
    [Output('department-dropdown', 'options'),
     Output('working-count', 'children'),
     Output('not-working-count', 'children'),
     Output('under-repair-count', 'children'),
     Output('total-count', 'children'),
     Output('status-chart', 'figure'),
     Output('department-chart', 'figure'),
     Output('equipment-types-chart', 'figure'),
     Output('interval-component', 'interval'),
     Output('interval-component', 'disabled')],
    [Input('chart-type-dropdown', 'value'),
     Input('department-dropdown', 'value'),
     Input('color-scheme-dropdown', 'value'),
     Input('refresh-interval', 'value'),
     Input('interval-component', 'n_intervals'),
     Input('session-id', 'children')]
)
def update_dashboard(chart_type, selected_department, color_scheme, refresh_interval, n_intervals, session_data):
    try:
        # Get equipment data (in production, you'd get workshop from session/user context)
        df = get_equipment_data(department=selected_department)
        
        # Handle empty data
        if df.empty:
            empty_fig = create_empty_figure("No equipment data available")
            dept_options = get_departments_for_workshop()
            
            return (
                dept_options,  # department options
                "0",          # working count
                "0",          # not working count  
                "0",          # under repair count
                "0",          # total count
                empty_fig,    # status chart
                empty_fig,    # department chart
                empty_fig,    # equipment types chart
                refresh_interval or 60000,  # interval
                refresh_interval == 0       # disabled
            )
        
        # Calculate counts
        status_counts = df['status'].value_counts()
        working_count = status_counts.get('Working', 0)
        not_working_count = status_counts.get('Not working', 0)
        under_repair_count = status_counts.get('Under repair', 0)
        total_count = len(df)
        
        # Get department options
        dept_options = get_departments_for_workshop()
        
        # Create charts
        status_chart = create_status_chart(df, chart_type, color_scheme)
        department_chart = create_department_chart(df, color_scheme)
        equipment_types_chart = create_equipment_types_chart(df, color_scheme)
        
        return (
            dept_options,
            str(working_count),
            str(not_working_count),
            str(under_repair_count),
            str(total_count),
            status_chart,
            department_chart,
            equipment_types_chart,
            refresh_interval or 60000,
            refresh_interval == 0
        )
        
    except Exception as e:
        print(f"Error in dashboard callback: {e}")
        # Return safe defaults on error
        empty_fig = create_empty_figure("Error loading data")
        
        return (
            [],           # department options
            "Error",      # working count
            "Error",      # not working count
            "Error",      # under repair count
            "Error",      # total count
            empty_fig,    # status chart
            empty_fig,    # department chart
            empty_fig,    # equipment types chart
            60000,        # interval
            True          # disabled
        )

# Session data callback for user context
@app.callback(
    Output('session-id', 'children'),
    [Input('session-id', 'id')]
)
def load_session_data(_):
    """Load session data - this would be populated by Django context"""
    # In production, you would get this from the Django request context
    # For now, return empty data
    return json.dumps({
        'workshop_id': None,
        'user_role': 'Tech',
        'department_filter': None
    })

# Theme sync - reads data-theme attribute from <html> via clientside callback
app.clientside_callback(
    """
    function(n_intervals) {
        var theme = document.documentElement.getAttribute('data-theme') || 'light';
        return {theme: theme};
    }
    """,
    Output('theme-data', 'data'),
    Input('theme-poll', 'n_intervals'),
    prevent_initial_call=False
)


def _apply_theme_to_figure(fig, theme='light'):
    if theme == 'dark':
        fig.update_layout(
            plot_bgcolor='#111111',
            paper_bgcolor='#111111',
            font=dict(color='#f1f5f9', size=14),
            title_font=dict(color='#f1f5f9', size=18),
            xaxis=dict(
                gridcolor='#1e1e1e',
                linecolor='#1e1e1e',
                tickfont=dict(color='#94a3b8')
            ),
            yaxis=dict(
                gridcolor='#1e1e1e',
                linecolor='#1e1e1e',
                tickfont=dict(color='#94a3b8')
            ),
            legend=dict(
                font=dict(color='#f1f5f9'),
                bgcolor='rgba(0,0,0,0)'
            )
        )
    else:
        fig.update_layout(
            plot_bgcolor='white',
            paper_bgcolor='white',
            font=dict(color='#0f2318', size=14),
            title_font=dict(color='#2c3e50', size=18),
            xaxis=dict(
                gridcolor='#e8f5ee',
                linecolor='#d1fae5',
                tickfont=dict(color='#4b5563')
            ),
            yaxis=dict(
                gridcolor='#e8f5ee',
                linecolor='#d1fae5',
                tickfont=dict(color='#4b5563')
            ),
            legend=dict(
                font=dict(color='#0f2318'),
                bgcolor='rgba(255,255,255,0)'
            )
        )
    return fig



app.clientside_callback(
    """
    function(theme_data) {
        if (!theme_data) return window.dash_clientside.no_update;
        var theme = theme_data.theme || 'light';
        
        var isDark = theme === 'dark';
        
        function updateChart(id) {
            var el = document.getElementById(id);
            if (!el || !window.Plotly) return;
            try {
                var fig = window.Plotly.relayout(el, {
                    'plot_bgcolor': isDark ? '#111111' : 'white',
                    'paper_bgcolor': isDark ? '#111111' : 'white'
                });
                
                var textColor = isDark ? '#f1f5f9' : '#0f2318';
                var gridColor = isDark ? '#1e1e1e' : '#e8f5ee';
                var tickColor = isDark ? '#94a3b8' : '#4b5563';
                
                window.Plotly.relayout(el, {
                    'xaxis.gridcolor': gridColor,
                    'xaxis.linecolor': gridColor,
                    'xaxis.tickfont.color': tickColor,
                    'yaxis.gridcolor': gridColor,
                    'yaxis.linecolor': gridColor,
                    'yaxis.tickfont.color': tickColor,
                    'titlefont.color': textColor,
                    'legend.font.color': textColor
                });
                
                var update = {'font': {'color': textColor}};
                window.Plotly.relayout(el, update);
            } catch(e) {}
        }
        
        setTimeout(function() {
            updateChart('status-chart');
            updateChart('department-chart');
            updateChart('equipment-types-chart');
        }, 100);
        
        return theme_data;
    }
    """,
    Output('theme-data', 'data', allow_duplicate=True),
    Input('theme-data', 'data'),
    prevent_initial_call=True
)

# Security settings
app.csrf_protect = False  # Disable CSRF for development
# Uncomment the line below for production:
# app = dash_login_required(app)