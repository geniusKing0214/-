.page-wrap {
    max-width: 1100px;
    margin: 40px auto;
    padding: 0 20px;
}

.page-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 24px;
    gap: 12px;
}

.card-list {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 20px;
}

.card {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 16px;
    box-shadow: 0 8px 20px rgba(0, 0, 0, 0.05);
}

.card-body {
    padding: 20px;
}

.card h2 {
    margin: 0 0 12px;
    font-size: 22px;
}

.text-muted {
    color: #6b7280;
    margin-bottom: 16px;
}

.info-row {
    margin-bottom: 10px;
    font-size: 15px;
}

.card-actions,
.detail-actions {
    margin-top: 18px;
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
}

.btn {
    display: inline-block;
    padding: 10px 16px;
    border-radius: 10px;
    text-decoration: none;
    border: none;
    cursor: pointer;
    font-size: 14px;
}

.btn-primary {
    background: #2563eb;
    color: #fff;
}

.btn-outline {
    background: #fff;
    border: 1px solid #d1d5db;
    color: #111827;
}

.btn-danger {
    background: #dc2626;
    color: #fff;
}

.btn-disabled {
    background: #9ca3af;
    color: #fff;
    cursor: not-allowed;
}

.badge {
    display: inline-flex;
    align-items: center;
    padding: 8px 12px;
    border-radius: 999px;
    font-size: 13px;
    font-weight: 600;
}

.badge.success {
    background: #dcfce7;
    color: #166534;
}

.empty-box {
    background: #f9fafb;
    border: 1px dashed #d1d5db;
    padding: 30px;
    border-radius: 16px;
    text-align: center;
    color: #6b7280;
}

.detail-card {
    background: #fff;
    border: 1px solid #e5e7eb;
    border-radius: 18px;
    padding: 28px;
    box-shadow: 0 8px 20px rgba(0, 0, 0, 0.05);
}

.detail-item {
    margin-bottom: 18px;
}

.detail-item strong {
    display: block;
    margin-bottom: 6px;
}