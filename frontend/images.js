// Requires: browser platform only
function findCloudinaryDeliveryBoundary(pathSegments, uploadIndex) {
    for (let index = uploadIndex + 1; index < pathSegments.length; index += 1) {
        if (/^v[0-9]+$/.test(pathSegments[index]) || pathSegments[index] === 'anime_covers') {
            return index;
        }
    }
    return -1;
}
function buildCloudinaryTransformationUrl(sourceUrl, transformation) {
    let imageUrl;
    try {
        imageUrl = new URL(sourceUrl);
    } catch (error) {
        return null;
    }

    if (imageUrl.protocol !== 'https:' || imageUrl.hostname !== 'res.cloudinary.com') {
        return null;
    }

    const pathSegments = imageUrl.pathname.split('/');
    const uploadIndex = pathSegments.findIndex((segment, index) => (
        segment === 'upload' && pathSegments[index - 1] === 'image'
    ));
    if (uploadIndex < 0) return null;

    const deliveryBoundary = findCloudinaryDeliveryBoundary(pathSegments, uploadIndex);
    if (deliveryBoundary < 0 || deliveryBoundary >= pathSegments.length - 1) return null;

    imageUrl.pathname = pathSegments
        .slice(0, uploadIndex + 1)
        .concat(transformation, pathSegments.slice(deliveryBoundary))
        .join('/');
    return imageUrl.toString();
}
