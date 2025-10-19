package tema1.t03;

import java.util.Scanner;

public class ej12 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        final double IVA = 0.21;

        System.out.print("Introduce el precio del producto: ");
        double precio = sc.nextDouble();

        double precioFinal = precio + (precio * IVA);

        System.out.println("El precio final con IVA es: " + precioFinal);
    }
}
