import Testing
@testable import ShoppingGuideKit

@Suite("ProductNavigationSelection")
struct ProductNavigationSelectionTests {
    @Test func selectingACardStoresExactlyOneDestinationProductID() {
        var selection = ProductNavigationSelection()

        selection.select(productID: "p_huawei")
        #expect(selection.destination?.productID == "p_huawei")

        selection.select(productID: "p_apple")
        #expect(selection.destination?.productID == "p_apple")
    }

    @Test func clearingSelectionRemovesDestination() {
        var selection = ProductNavigationSelection()

        selection.select(productID: "p_huawei")
        selection.clear()

        #expect(selection.destination == nil)
    }
}
